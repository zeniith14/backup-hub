"""Parsing dell'output JSON di rclone (--use-json-log)."""
from __future__ import annotations

import json
from typing import Any


def parse_line(line: str) -> tuple[str, Any]:
    """Ritorna (kind, data).

    kind:
      "stats" -> dict con i progressi
      "log"   -> dict {level, msg, object}
      "raw"   -> str (riga non JSON)
    """
    line = line.rstrip("\n")
    if not line.strip():
        return "raw", ""
    if not line.startswith("{"):
        return "raw", line
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return "raw", line
    if isinstance(d.get("stats"), dict):
        return "stats", normalize_stats(d["stats"])
    return "log", {
        "level": str(d.get("level", "info")).lower(),
        "msg": str(d.get("msg", "")).strip(),
        "object": d.get("object") or "",
    }


def normalize_stats(s: dict) -> dict:
    transferring = []
    for t in s.get("transferring") or []:
        transferring.append(
            {
                "name": str(t.get("name", "")),
                "size": int(t.get("size") or 0),
                "bytes": int(t.get("bytes") or 0),
                "percentage": int(t.get("percentage") or 0),
                "speed": float(t.get("speed") or 0),
            }
        )
    eta = s.get("eta")
    return {
        "bytes": int(s.get("bytes") or 0),
        "total_bytes": int(s.get("totalBytes") or 0),
        "speed": float(s.get("speed") or 0),
        "eta": float(eta) if eta is not None else None,
        "transfers": int(s.get("transfers") or 0),
        "total_transfers": int(s.get("totalTransfers") or 0),
        "checks": int(s.get("checks") or 0),
        "total_checks": int(s.get("totalChecks") or 0),
        "errors": int(s.get("errors") or 0),
        "deletes": int(s.get("deletes") or 0),
        "elapsed": float(s.get("elapsedTime") or 0),
        "last_error": str(s.get("lastError") or ""),
        "transferring": transferring[:6],
    }
