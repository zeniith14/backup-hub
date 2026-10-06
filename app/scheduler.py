"""Scheduler cron minimale (un thread, controlla ogni pochi secondi)."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from .config import Config
from .db import DB
from .runner import BackupManager, Busy

log = logging.getLogger("backup-hub.scheduler")


class Scheduler:
    def __init__(self, cfg: Config, db: DB, manager: BackupManager, tick: float = 10.0):
        self.cfg = cfg
        self.db = db
        self.manager = manager
        self.tick = tick
        self.tz = ZoneInfo(cfg.timezone)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next: datetime | None = None
        self._applied: tuple[str, bool] | None = None
        self._lock = threading.RLock()

    # --- impostazioni (la UI può sovrascrivere quelle del file) -------------
    def settings(self) -> tuple[str, bool]:
        cron = self.db.get_setting("schedule", self.cfg.schedule) or self.cfg.schedule
        enabled = (self.db.get_setting("schedule_enabled", "1" if self.cfg.schedule_enabled else "0")) == "1"
        return cron, enabled

    def update(self, cron: str, enabled: bool) -> None:
        if not croniter.is_valid(cron):
            raise ValueError("espressione cron non valida")
        self.db.set_setting("schedule", cron)
        self.db.set_setting("schedule_enabled", "1" if enabled else "0")
        self._recompute()

    def preview(self, cron: str, n: int = 3) -> list[str]:
        if not croniter.is_valid(cron):
            raise ValueError("espressione cron non valida")
        it = croniter(cron, datetime.now(self.tz))
        return [it.get_next(datetime).isoformat() for _ in range(n)]

    def info(self) -> dict:
        cron, enabled = self.settings()
        with self._lock:
            nxt = self._next if enabled else None
        return {"cron": cron, "enabled": enabled, "next_run": nxt.isoformat() if nxt else None}

    # --- ciclo ---------------------------------------------------------------
    def _recompute(self) -> None:
        cron, enabled = self.settings()
        with self._lock:
            self._applied = (cron, enabled)
            self._next = croniter(cron, datetime.now(self.tz)).get_next(datetime) if enabled else None

    def _loop(self) -> None:
        self._recompute()
        while not self._stop.wait(self.tick):
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("errore nello scheduler")

    def run_once(self) -> None:
        if self.settings() != self._applied:
            self._recompute()
        with self._lock:
            nxt = self._next
        if nxt is None or datetime.now(self.tz) < nxt:
            return
        try:
            self.manager.start(trigger="automatico")
            log.info("backup automatico avviato")
        except Busy:
            log.info("backup automatico saltato: già in corso")
        self._recompute()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
