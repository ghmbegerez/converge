"""Best-effort notification dispatch. Never raises."""

from __future__ import annotations

import logging

from converge.feature_flags import get_mode, is_enabled

log = logging.getLogger("converge.notifications")

_adapter = None


def notify(event_type: str, payload: dict, channel: str = "default") -> None:
    """Fire-and-forget notification. Never raises."""
    if not is_enabled("notifications"):
        return

    mode = get_mode("notifications")
    if mode == "shadow":
        log.debug("Shadow notification: %s %s", event_type, payload)
        return

    global _adapter
    if _adapter is None:
        from converge.notifications.webhook_adapter import WebhookNotifyAdapter

        _adapter = WebhookNotifyAdapter()

    try:
        _adapter.send(channel, event_type, payload)
    except Exception:
        log.exception("Notification dispatch failed for %s", event_type)


def reset_adapter() -> None:
    """Reset the adapter (for tests)."""
    global _adapter
    _adapter = None
