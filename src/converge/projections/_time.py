"""Shared time utilities for projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _since_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _since_days(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
