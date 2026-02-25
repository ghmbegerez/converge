"""HTTP POST webhook adapter with HMAC-SHA256 signing."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from converge import event_log
from converge.models import Event, EventType, now_iso

log = logging.getLogger("converge.notifications.webhook")


class WebhookNotifyAdapter:
    """HTTP POST webhook with HMAC-SHA256 signing."""

    def __init__(self) -> None:
        self._urls = self._load_config()
        self._secret = os.environ.get("CONVERGE_WEBHOOK_SECRET", "")

    def _load_config(self) -> dict[str, str]:
        for p in [Path(".converge/notifications.json"), Path("notifications.json")]:
            if p.exists():
                try:
                    data = json.loads(p.read_text())
                    return data.get("webhooks", {})
                except (json.JSONDecodeError, IOError):
                    pass
        url = os.environ.get("CONVERGE_WEBHOOK_URL", "")
        return {"default": url} if url else {}

    def send(self, channel: str, event_type: str, payload: dict[str, Any]) -> bool:
        url = self._urls.get(channel) or self._urls.get("default", "")
        if not url:
            return False

        body = json.dumps({
            "event_type": event_type,
            "payload": payload,
            "timestamp": now_iso(),
        })
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._secret:
            sig = hmac.new(
                self._secret.encode(), body.encode(), hashlib.sha256,
            ).hexdigest()
            headers["X-Converge-Signature"] = f"sha256={sig}"

        import httpx

        for attempt in range(2):  # 1 retry
            try:
                resp = httpx.post(url, content=body, headers=headers, timeout=10)
                if resp.status_code < 400:
                    event_log.append(Event(
                        event_type=EventType.NOTIFICATION_SENT,
                        payload={
                            "channel": channel,
                            "event_type": event_type,
                            "url": url,
                            "status_code": resp.status_code,
                        },
                    ))
                    return True
            except Exception:
                if attempt == 0:
                    time.sleep(1)

        event_log.append(Event(
            event_type=EventType.NOTIFICATION_FAILED,
            payload={
                "channel": channel,
                "event_type": event_type,
                "url": url,
            },
        ))
        return False

    def is_available(self) -> bool:
        return bool(self._urls.get("default"))
