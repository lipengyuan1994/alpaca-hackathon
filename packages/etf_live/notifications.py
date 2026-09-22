"""Operational-only Telegram notification outbox."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from packages.runtime_secrets import require_file_secret

from .state import LiveState


class TelegramNotifier:
    def __init__(self, *, state: LiveState, secrets_root: Path = Path("/run/etf-live-secrets"), enabled: bool = True) -> None:
        self.state = state
        self.secrets_root = secrets_root
        self.enabled = enabled

    def queue(self, *, incident_id: str, kind: str, message: str) -> None:
        self.state.queue_notification(incident_id, kind, {"text": message[:3500]})

    def flush(self) -> None:
        if not self.enabled:
            return
        try:
            token = require_file_secret("TELEGRAM_BOT_TOKEN", environ=os.environ, allowed_roots=(self.secrets_root,))
            chat_id = require_file_secret("TELEGRAM_CHAT_ID", environ=os.environ, allowed_roots=(self.secrets_root,))
        except Exception:
            return
        client = httpx.Client(timeout=8)
        for row in self.state.pending_notifications():
            try:
                response = client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": json.loads(row["payload_json"])["text"], "disable_web_page_preview": True})
                response.raise_for_status()
            except Exception:
                self.state.mark_notification(row["incident_id"], status="pending")
            else:
                self.state.mark_notification(row["incident_id"], status="sent")
