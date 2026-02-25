"""Notification port: protocol definition for notification adapters."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class NotifyPort(Protocol):
    """Protocol for outbound notification adapters."""

    def send(self, channel: str, event_type: str, payload: dict[str, Any]) -> bool: ...

    def is_available(self) -> bool: ...
