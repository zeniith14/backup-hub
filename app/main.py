"""API FastAPI + file statici della dashboard."""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import Config, load_config
from .db import DB
from .notify import Notifier
from .runner import Busy, BackupManager
from .scheduler import Scheduler

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("backup-hub")


class RunRequest(BaseModel):
    jobs: list[str] | None = None
    dry_run: bool = False


class ScheduleRequest(BaseModel):
    cron: str
    enabled: bool = True


def create_app(cfg: Config | None = None, start_scheduler: bool = True) -> FastAPI:
    cfg = cfg or load_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    db = DB(cfg.data_dir / "backup-hub.db")
    notifier = Notifier(cfg.notify)
    manager = BackupManager(cfg, db, notifier)
    scheduler = Scheduler(cfg, db, manager)
    tz = ZoneInfo(cfg.timezone)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_scheduler:
            scheduler.start()
        if not cfg.password:
            log.warning("ATTENZIONE: nessuna password impostata, la dashboard è aperta a tutta la rete")
        yield
        scheduler.stop()

    app = FastAPI(title="Backup Hub", version=__version__, lifespan=lifespan)
    app.state.cfg, app.state.db, app.state.manager, app.state.scheduler = cfg, db, manager, scheduler

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if cfg.password:
            ok = False
            header = request.headers.get("authorization", "")
            if header.lower().startswith("basic "):
                try:
                    _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                    ok = secrets.compare_digest(pw.encode(), cfg.password.encode())
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                await asyncio.sleep(1)  # rallenta i tentativi
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Backup Hub"'})
        return await call_next(request)

    # ------------------------------------------------------------------ API
    @app.get("/api/status")
    def status():
        snap = manager.snapshot()
        jobs = []
        total_est, all_known = 0.0, True
        for j in cfg.jobs:
            est = db.estimate(j.name)
            if est is None:
                all_known = False
            else:
                total_est += est
            jobs.append(
                {
                    "name": j.name,
                    "label": j.label,
                    "source": j.source,
                    "dest": j.dest,
                    "mode": j.mode,
                    "estimate": est,
                    "last_run": db.last_run(j.name),
                    "history": [r["duration"] for r in reversed(db.list_runs(12, j.name)) if r["status"] == "ok" and not r["dry_run"] and r["duration"]],
                }
            )
        sched = scheduler.info()
        return {
            "now": datetime.now(tz).isoformat(),
            "timezone": cfg.timezone,
            "version": __version__,
            "auth": bool(cfg.password),
            "notifications": notifier.enabled,
            "schedule": sched,
            "next_estimate": total_est if all_known else None,
            "last_run": db.last_run(),
            "jobs": jobs,
            "manager": snap,
        }

    @app.get("/api/runs")
    def runs(limit: int = 50, job: str | None = None):
        return db.list_runs(min(max(limit, 1), 500), job)

    @app.get("/api/runs/{run_id}/log")
    def run_log(run_id: int, tail: int = 400):
        run = db.get_run(run_id)
        if not run or not run.get("log_path"):
            raise HTTPException(404, "log non trovato")
        p = Path(run["log_path"]).resolve()
        if (cfg.data_dir / "logs").resolve() not in p.parents or not p.is_file():
            raise HTTPException(404, "log non trovato")
        lines = p.read_text(errors="replace").splitlines()
        tail = min(max(tail, 10), 5000)
        return {"run": run, "truncated": len(lines) > tail, "lines": lines[-tail:]}

    @app.post("/api/run")
    def run(req: RunRequest):
        try:
            batch = manager.start(req.jobs, trigger="manuale", dry_run=req.dry_run)
        except Busy as e:
            raise HTTPException(409, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "batch": batch}

    @app.post("/api/cancel")
    def cancel():
        if not manager.cancel():
            raise HTTPException(409, "Nessun backup in corso")
        return {"ok": True}

    @app.get("/api/schedule/preview")
    def schedule_preview(cron: str):
        try:
            return {"next": scheduler.preview(cron)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/schedule")
    def schedule_update(req: ScheduleRequest):
        try:
            scheduler.update(req.cron.strip(), req.enabled)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return scheduler.info()

    @app.post("/api/test-notify")
    def test_notify():
        if not notifier.enabled:
            raise HTTPException(400, "Nessun canale di notifica configurato")
        errors = notifier.send("Backup Hub", f"Notifica di prova ({time.strftime('%H:%M:%S')})", ok=True)
        if errors:
            raise HTTPException(502, "; ".join(errors))
        return {"ok": True}

    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
    return app
