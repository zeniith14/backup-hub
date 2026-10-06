"""Notifiche opzionali: ntfy, Telegram, webhook generico."""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger("backup-hub.notify")


class Notifier:
    def __init__(self, cfg: dict):
        self.cfg = cfg or {}

    @property
    def enabled(self) -> bool:
        c = self.cfg
        return bool(c.get("ntfy_url") or (c.get("telegram_token") and c.get("telegram_chat_id")) or c.get("webhook_url"))

    def send(self, title: str, message: str, ok: bool) -> list[str]:
        """Invia su tutti i canali configurati. Ritorna la lista di errori (vuota = ok)."""
        errors: list[str] = []
        c = self.cfg
        if c.get("ntfy_url"):
            try:
                req = urllib.request.Request(
                    c["ntfy_url"],
                    data=message.encode(),
                    headers={
                        "Title": title.encode("ascii", "ignore").decode() or "Backup Hub",
                        "Priority": "default" if ok else "high",
                        "Tags": "white_check_mark" if ok else "rotating_light",
                    },
                )
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:  # noqa: BLE001
                errors.append(f"ntfy: {e}")
        if c.get("telegram_token") and c.get("telegram_chat_id"):
            try:
                body = json.dumps({"chat_id": c["telegram_chat_id"], "text": f"{title}\n{message}"}).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{c['telegram_token']}/sendMessage",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:  # noqa: BLE001
                errors.append(f"telegram: {e}")
        if c.get("webhook_url"):
            try:
                body = json.dumps({"title": title, "message": message, "ok": ok}).encode()
                req = urllib.request.Request(
                    c["webhook_url"], data=body, headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:  # noqa: BLE001
                errors.append(f"webhook: {e}")
        for e in errors:
            log.warning("notifica fallita: %s", e)
        return errors

    def batch_done(self, results: list[dict], trigger: str, dry_run: bool) -> None:
        if not self.enabled or dry_run:
            return
        failed = [r for r in results if r["status"] not in ("ok",)]
        if failed:
            lines = [f"- {r['label']}: {r['status']}" + (f" ({r['error']})" if r.get("error") else "") for r in failed]
            self.send("Backup FALLITO", "\n".join(lines), ok=False)
        elif self.cfg.get("on_success"):
            lines = [f"- {r['label']}: ok ({r['bytes_h']}, {r['duration_h']})" for r in results]
            self.send("Backup completato", "\n".join(lines), ok=True)
