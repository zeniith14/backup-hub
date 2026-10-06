#!/usr/bin/env python3
"""rclone finto per i test e per la demo: emette lo stesso JSON di `rclone --use-json-log`.

Comportamento via variabili d'ambiente:
  FAKE_MODE   ok | fail | slow | notfound-dir   (default ok)
  FAKE_STEPS  numero di righe stats (default 4)
  FAKE_DELAY  secondi tra una riga e l'altra (default 0.05)
"""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_MODE", "ok")
steps = int(os.environ.get("FAKE_STEPS", "4"))
delay = float(os.environ.get("FAKE_DELAY", "0.05"))
if mode == "slow":
    steps, delay = 600, 0.2

total_bytes = 5 * 1024**3
total_files = 1200


def emit(obj):
    print(json.dumps(obj), file=sys.stderr, flush=True)


for i in range(1, steps + 1):
    frac = i / steps
    emit(
        {
            "level": "info",
            "msg": "stats",
            "stats": {
                "bytes": int(total_bytes * frac),
                "totalBytes": total_bytes,
                "speed": 8_500_000.0,
                "eta": int((1 - frac) * 600) if i > 1 else None,
                "transfers": int(total_files * frac),
                "totalTransfers": total_files,
                "checks": 20000,
                "totalChecks": 20000,
                "errors": 0,
                "elapsedTime": i * delay,
                "transferring": [
                    {"name": "library/abc/2026/IMG_0001.jpg", "size": 4_200_000, "bytes": 2_000_000, "percentage": 47, "speed": 3_000_000.0}
                ],
            },
        }
    )
    time.sleep(delay)

if mode == "fail":
    emit({"level": "error", "msg": "Failed to copy: googleapi: Error 403: Rate Limit Exceeded", "object": "library/x.jpg"})
    emit({"level": "error", "msg": "Attempt 3/3 failed with 1 errors"})
    sys.exit(1)

emit({"level": "notice", "msg": "Transferred: done"})
sys.exit(0)
