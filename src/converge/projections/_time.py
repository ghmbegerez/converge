"""Shared utilities for projections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _since_hours(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _since_days(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _safe_avg(values: list[float]) -> float:
    """Average of a list, returning 0.0 for empty lists."""
    return sum(values) / len(values) if values else 0.0
