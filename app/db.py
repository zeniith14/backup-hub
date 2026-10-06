"""Storico dei backup (SQLite)."""
from __future__ import annotations

import sqlite3
import statistics
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    trigger TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    rc INTEGER,
    bytes INTEGER DEFAULT 0,
    transfers INTEGER DEFAULT 0,
    checks INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    last_error TEXT,
    log_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job, started_at DESC);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class DB:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            # Run rimasti "running" per un riavvio/crash del servizio
            self.conn.execute(
                "UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'",
                (time.time(),),
            )
            self.conn.commit()

    # ---- runs -------------------------------------------------------------
    def create_run(self, job: str, trigger: str, dry_run: bool, log_path: str) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO runs(job, trigger, dry_run, status, started_at, log_path) "
                "VALUES (?,?,?,?,?,?)",
                (job, trigger, int(dry_run), "running", time.time(), log_path),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, rc: int | None, stats: dict, last_error: str | None):
        with self.lock:
            self.conn.execute(
                "UPDATE runs SET status=?, finished_at=?, rc=?, bytes=?, transfers=?, checks=?, "
                "errors=?, last_error=? WHERE id=?",
                (
                    status,
                    time.time(),
                    rc,
                    int(stats.get("bytes") or 0),
                    int(stats.get("transfers") or 0),
                    int(stats.get("checks") or 0),
                    int(stats.get("errors") or 0),
                    (last_error or None) and last_error[:500],
                    run_id,
                ),
            )
            self.conn.commit()

    def _row(self, r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        d["dry_run"] = bool(d["dry_run"])
        d["duration"] = (d["finished_at"] - d["started_at"]) if d["finished_at"] else None
        return d

    def get_run(self, run_id: int) -> dict | None:
        with self.lock:
            return self._row(self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def list_runs(self, limit: int = 50, job: str | None = None) -> list[dict]:
        q = "SELECT * FROM runs"
        args: list = []
        if job:
            q += " WHERE job=?"
            args.append(job)
        q += " ORDER BY started_at DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            return [self._row(r) for r in self.conn.execute(q, args).fetchall()]

    def last_run(self, job: str | None = None, include_dry: bool = False) -> dict | None:
        q = "SELECT * FROM runs WHERE status!='running'"
        args: list = []
        if not include_dry:
            q += " AND dry_run=0"
        if job:
            q += " AND job=?"
            args.append(job)
        q += " ORDER BY started_at DESC LIMIT 1"
        with self.lock:
            return self._row(self.conn.execute(q, args).fetchone())

    def estimate(self, job: str, n: int = 5) -> float | None:
        """Durata stimata = mediana delle ultime n esecuzioni riuscite (non dry-run)."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT finished_at - started_at AS d FROM runs "
                "WHERE job=? AND status='ok' AND dry_run=0 AND finished_at IS NOT NULL "
                "ORDER BY started_at DESC LIMIT ?",
                (job, n),
            ).fetchall()
        durs = [r["d"] for r in rows if r["d"] and r["d"] > 0]
        return statistics.median(durs) if durs else None

    def prune(self, keep: int) -> list[str]:
        """Tiene le ultime `keep` esecuzioni; ritorna i log da cancellare."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, log_path FROM runs ORDER BY started_at DESC LIMIT -1 OFFSET ?", (keep,)
            ).fetchall()
            if rows:
                self.conn.executemany("DELETE FROM runs WHERE id=?", [(r["id"],) for r in rows])
                self.conn.commit()
        return [r["log_path"] for r in rows if r["log_path"]]

    # ---- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.lock:
            r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self.conn.commit()
