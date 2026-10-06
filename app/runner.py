"""Esecuzione dei job rclone, con progresso in tempo reale."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .config import Config, Job
from .db import DB
from .notify import Notifier
from .parse import parse_line

log = logging.getLogger("backup-hub.runner")


class Busy(Exception):
    """C'è già un backup in corso."""


def human_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_dur(sec: float | None) -> str:
    if sec is None:
        return "-"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def build_cmd(cfg: Config, job: Job, dry_run: bool) -> list[str]:
    cmd = [cfg.rclone_binary, job.mode, job.source, job.dest]
    cmd += cfg.rclone_flags
    cmd += ["--use-json-log", "--stats", "2s", "--log-level", "INFO"]
    for ex in job.excludes:
        cmd += ["--exclude", ex]
    if job.mode == "sync" and job.max_delete is not None:
        cmd += ["--max-delete", str(job.max_delete)]
    cmd += job.extra_flags
    if dry_run:
        cmd.append("--dry-run")
    return cmd


class BackupManager:
    def __init__(self, cfg: Config, db: DB, notifier: Notifier | None = None):
        self.cfg = cfg
        self.db = db
        self.notifier = notifier or Notifier({})
        self._lock = threading.RLock()
        self._batch: dict | None = None
        self._proc: subprocess.Popen | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        (cfg.data_dir / "logs").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ API
    def is_busy(self) -> bool:
        with self._lock:
            return self._batch is not None

    def start(self, job_names: list[str] | None = None, trigger: str = "manuale", dry_run: bool = False) -> str:
        names = job_names or [j.name for j in self.cfg.jobs]
        unknown = [n for n in names if not self.cfg.job(n)]
        if unknown:
            raise ValueError(f"job sconosciuti: {', '.join(unknown)}")
        # mantiene l'ordine di configurazione, senza duplicati
        names = [j.name for j in self.cfg.jobs if j.name in set(names)]
        with self._lock:
            if self._batch is not None:
                raise Busy("Un backup è già in corso")
            self._cancel.clear()
            batch_id = datetime.now().strftime("%Y%m%d-%H%M%S")
            self._batch = {
                "id": batch_id,
                "trigger": trigger,
                "dry_run": dry_run,
                "queue": list(names),
                "done": [],
                "started_at": time.time(),
                "current": None,
                "log": deque(maxlen=200),
            }
            self._thread = threading.Thread(target=self._worker, args=(names, trigger, dry_run), daemon=True)
            self._thread.start()
        return batch_id

    def cancel(self) -> bool:
        with self._lock:
            if self._batch is None:
                return False
            self._cancel.set()
            proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

            def _kill():
                time.sleep(10)
                if proc.poll() is None:
                    proc.kill()

            threading.Thread(target=_kill, daemon=True).start()
        return True

    def wait(self, timeout: float | None = None) -> None:
        t = self._thread
        if t:
            t.join(timeout)

    # ------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        with self._lock:
            b = self._batch
            if b is None:
                return {"running": False}
            now = time.time()
            cur = b["current"]
            current = None
            batch_eta = None
            if cur:
                p = dict(cur["progress"])
                elapsed = now - cur["started_at"]
                eta, eta_src = p.get("eta"), "rclone"
                if eta is None and p.get("total_bytes") and p.get("speed"):
                    eta = max(p["total_bytes"] - p["bytes"], 0) / p["speed"]
                    eta_src = "velocità"
                est = cur.get("estimate")
                if eta is None and est:
                    eta, eta_src = max(est - elapsed, 0), "storico"
                percent = None
                if p.get("total_bytes"):
                    percent = min(100.0, 100.0 * p["bytes"] / p["total_bytes"])
                phase = "avvio"
                if p:
                    if p.get("transfers") or p.get("total_transfers"):
                        phase = "trasferimento"
                    elif p.get("total_checks") and p.get("checks") < p.get("total_checks"):
                        phase = "confronto file"
                    else:
                        phase = "confronto file"
                current = {
                    "job": cur["job"],
                    "label": cur["label"],
                    "run_id": cur["run_id"],
                    "started_at": cur["started_at"],
                    "elapsed": elapsed,
                    "estimate": est,
                    "eta": eta,
                    "eta_source": eta_src if eta is not None else None,
                    "percent": percent,
                    "phase": phase,
                    "progress": p,
                }
                if eta is not None:
                    batch_eta = eta
            queued_est = 0.0
            queued_known = True
            for n in b["queue"]:
                e = self.db.estimate(n)
                if e is None:
                    queued_known = False
                else:
                    queued_est += e
            total_eta = None
            if current is not None and batch_eta is not None and queued_known:
                total_eta = batch_eta + queued_est
            elif current is None and queued_known and b["queue"]:
                total_eta = queued_est
            return {
                "running": True,
                "batch_id": b["id"],
                "trigger": b["trigger"],
                "dry_run": b["dry_run"],
                "started_at": b["started_at"],
                "cancelling": self._cancel.is_set(),
                "queue": [{"job": n, "label": self.cfg.job(n).label} for n in b["queue"]],
                "done": list(b["done"]),
                "current": current,
                "total_eta": total_eta,
                "log_tail": list(b["log"])[-25:],
            }

    # --------------------------------------------------------------- worker
    def _worker(self, names: list[str], trigger: str, dry_run: bool) -> None:
        results: list[dict] = []
        try:
            for name in names:
                if self._cancel.is_set():
                    break
                with self._lock:
                    if self._batch and name in self._batch["queue"]:
                        self._batch["queue"].remove(name)
                res = self._run_job(self.cfg.job(name), trigger, dry_run)
                results.append(res)
                with self._lock:
                    if self._batch:
                        self._batch["done"].append({"job": name, "label": res["label"], "status": res["status"]})
        except Exception:  # noqa: BLE001
            log.exception("errore inatteso nel worker")
        finally:
            for p in self.db.prune(self.cfg.keep_runs):
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                self._batch = None
                self._proc = None
            try:
                self.notifier.batch_done(results, trigger, dry_run)
            except Exception:  # noqa: BLE001
                log.exception("notifica fallita")

    def _run_job(self, job: Job, trigger: str, dry_run: bool) -> dict:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = self.cfg.data_dir / "logs" / f"{stamp}-{job.name}{'-dry' if dry_run else ''}.log"
        run_id = self.db.create_run(job.name, trigger, dry_run, str(log_path))
        started = time.time()
        cur = {
            "job": job.name,
            "label": job.label,
            "run_id": run_id,
            "started_at": started,
            "progress": {},
            "estimate": self.db.estimate(job.name),
        }
        with self._lock:
            self._batch["current"] = cur
        cmd = build_cmd(self.cfg, job, dry_run)
        last_stats: dict = {}
        last_error: str | None = None
        status, rc = "failed", None
        last_stats_log = 0.0

        with open(log_path, "w", buffering=1) as lf:
            lf.write(f"# {datetime.now().isoformat(timespec='seconds')} job={job.name} trigger={trigger} dry_run={dry_run}\n")
            lf.write("# " + " ".join(cmd) + "\n")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env={**os.environ, "RCLONE_ASK_PASSWORD": "false"},
                )
            except FileNotFoundError:
                last_error = f"rclone non trovato ({self.cfg.rclone_binary})"
                lf.write(f"ERRORE: {last_error}\n")
                self.db.finish_run(run_id, "failed", None, {}, last_error)
                return self._result(job, "failed", {}, started, last_error)

            with self._lock:
                self._proc = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                kind, data = parse_line(line)
                if kind == "stats":
                    last_stats = data
                    with self._lock:
                        cur["progress"] = data
                    now = time.time()
                    if now - last_stats_log >= 30:
                        last_stats_log = now
                        lf.write(
                            f"{datetime.now():%H:%M:%S} STATS {human_bytes(data['bytes'])}/{human_bytes(data['total_bytes'])} "
                            f"file {data['transfers']}/{data['total_transfers']} controlli {data['checks']}/{data['total_checks']} "
                            f"errori {data['errors']}\n"
                        )
                elif kind == "log":
                    lvl = data["level"]
                    obj = f" [{data['object']}]" if data["object"] else ""
                    text = f"{datetime.now():%H:%M:%S} {lvl.upper()} {data['msg']}{obj}"
                    lf.write(text + "\n")
                    if lvl in ("error", "critical", "fatal"):
                        last_error = data["msg"][:300]
                    if lvl in ("error", "critical", "fatal", "warning", "notice") and data["msg"]:
                        with self._lock:
                            if self._batch:
                                self._batch["log"].append(f"{lvl.upper()} {data['msg']}{obj}"[:300])
                elif data:
                    lf.write(f"{datetime.now():%H:%M:%S} {data}\n")
                    with self._lock:
                        if self._batch:
                            self._batch["log"].append(str(data)[:300])
            rc = proc.wait()
            if self._cancel.is_set():
                status = "cancelled"
            elif rc == 0:
                status = "ok"
            else:
                status = "failed"
                last_error = last_error or last_stats.get("last_error") or f"rclone ha terminato con codice {rc}"
            lf.write(
                f"# FINE status={status} rc={rc} bytes={last_stats.get('bytes', 0)} "
                f"file={last_stats.get('transfers', 0)} errori={last_stats.get('errors', 0)}\n"
            )

        self.db.finish_run(run_id, status, rc, last_stats, last_error)
        return self._result(job, status, last_stats, started, last_error)

    @staticmethod
    def _result(job: Job, status: str, stats: dict, started: float, error: str | None) -> dict:
        dur = time.time() - started
        return {
            "job": job.name,
            "label": job.label,
            "status": status,
            "error": error,
            "bytes_h": human_bytes(stats.get("bytes", 0)),
            "duration_h": human_dur(dur),
        }
