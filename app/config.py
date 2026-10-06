"""Caricamento e validazione della configurazione (YAML + variabili d'ambiente)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from croniter import croniter

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


class ConfigError(Exception):
    pass


@dataclass
class Job:
    name: str
    label: str
    source: str
    dest: str
    mode: str = "sync"  # sync | copy
    excludes: list[str] = field(default_factory=list)
    max_delete: int | None = 100
    extra_flags: list[str] = field(default_factory=list)


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8080
    password: str = ""
    data_dir: Path = Path("/var/lib/backup-hub")
    schedule: str = "30 3 * * *"
    schedule_enabled: bool = True
    timezone: str = "Europe/Rome"
    rclone_binary: str = "rclone"
    rclone_flags: list[str] = field(default_factory=list)
    keep_runs: int = 500
    notify: dict = field(default_factory=dict)
    jobs: list[Job] = field(default_factory=list)

    def job(self, name: str) -> Job | None:
        return next((j for j in self.jobs if j.name == name), None)


def config_search_paths() -> list[str]:
    paths = [os.environ.get("BACKUP_HUB_CONFIG", "")]
    paths += ["/etc/backup-hub/config.yml", "./config.yml"]
    return [p for p in paths if p]


def load_config(path: str | os.PathLike | None = None) -> Config:
    candidates = [str(path)] if path else config_search_paths()
    chosen = next((p for p in candidates if Path(p).is_file()), None)
    if not chosen:
        raise ConfigError(
            "Nessun file di configurazione trovato. Cercato in: " + ", ".join(candidates)
        )
    raw = yaml.safe_load(Path(chosen).read_text()) or {}
    return parse_config(raw)


def parse_config(raw: dict) -> Config:
    server = raw.get("server") or {}
    rclone = raw.get("rclone") or {}
    cfg = Config(
        host=str(server.get("host", "0.0.0.0")),
        port=int(server.get("port", 8080)),
        password=os.environ.get("BACKUP_HUB_PASSWORD") or str((raw.get("auth") or {}).get("password") or ""),
        data_dir=Path(raw.get("data_dir", "/var/lib/backup-hub")),
        schedule=str(raw.get("schedule", "30 3 * * *")),
        schedule_enabled=bool(raw.get("schedule_enabled", True)),
        timezone=str(raw.get("timezone", "Europe/Rome")),
        rclone_binary=str(rclone.get("binary", "rclone")),
        rclone_flags=[str(x) for x in (rclone.get("flags") or [])],
        keep_runs=int(raw.get("keep_runs", 500)),
        notify=raw.get("notify") or {},
    )

    try:
        ZoneInfo(cfg.timezone)
    except Exception as e:  # noqa: BLE001
        raise ConfigError(f"timezone non valida: {cfg.timezone}") from e
    if not croniter.is_valid(cfg.schedule):
        raise ConfigError(f"schedule cron non valida: {cfg.schedule!r}")

    jobs_raw = raw.get("jobs") or []
    if not jobs_raw:
        raise ConfigError("Nessun job definito in 'jobs'")
    seen: set[str] = set()
    for j in jobs_raw:
        name = str(j.get("name", ""))
        if not NAME_RE.match(name):
            raise ConfigError(f"nome job non valido: {name!r} (usa a-z, 0-9, - e _)")
        if name in seen:
            raise ConfigError(f"nome job duplicato: {name}")
        seen.add(name)
        mode = str(j.get("mode", "sync"))
        if mode not in ("sync", "copy"):
            raise ConfigError(f"job {name}: mode deve essere 'sync' o 'copy'")
        if not j.get("source") or not j.get("dest"):
            raise ConfigError(f"job {name}: servono 'source' e 'dest'")
        md = j.get("max_delete", 100)
        cfg.jobs.append(
            Job(
                name=name,
                label=str(j.get("label", name)),
                source=str(j["source"]),
                dest=str(j["dest"]),
                mode=mode,
                excludes=[str(x) for x in (j.get("excludes") or [])],
                max_delete=None if md in (None, False) else int(md),
                extra_flags=[str(x) for x in (j.get("extra_flags") or [])],
            )
        )
    return cfg
