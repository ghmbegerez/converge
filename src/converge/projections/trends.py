"""Trend projections: risk, entropy, health time series + integration metrics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from converge import event_log
from converge.models import EventType, now_iso
from converge.projections._time import _since_days


def risk_trend(
    db_path: str | Path,
    tenant_id: str | None = None,
    days: int = 30,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Risk score time series from events."""
    since = _since_days(days)
    events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, tenant_id=tenant_id, since=since, limit=limit)
    return [{
        "timestamp": e["timestamp"],
        "intent_id": e["intent_id"],
        "risk_score": e["payload"].get("risk_score", 0),
        "damage_score": e["payload"].get("damage_score", 0),
        "entropy_score": e["payload"].get("entropy_score", 0),
    } for e in events]


def entropy_trend(
    db_path: str | Path,
    tenant_id: str | None = None,
    days: int = 30,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Entropy score time series."""
    since = _since_days(days)
    events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, tenant_id=tenant_id, since=since, limit=limit)
    return [{
        "timestamp": e["timestamp"],
        "intent_id": e["intent_id"],
        "entropy_score": e["payload"].get("entropy_score", 0),
    } for e in events]


def health_trend(
    db_path: str | Path,
    tenant_id: str | None = None,
    days: int = 30,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Health snapshot time series."""
    since = _since_days(days)
    events = event_log.query(db_path, event_type=EventType.HEALTH_SNAPSHOT, tenant_id=tenant_id, since=since, limit=limit)
    return [e["payload"] for e in events]


def change_health_trend(
    db_path: str | Path,
    tenant_id: str | None = None,
    days: int = 30,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Change-level health time series."""
    since = _since_days(days)
    events = event_log.query(db_path, event_type=EventType.HEALTH_CHANGE_SNAPSHOT, tenant_id=tenant_id, since=since, limit=limit)
    return [e["payload"] for e in events]


def integration_metrics(
    db_path: str | Path,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Compute integration metrics from event history."""
    sims = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, tenant_id=tenant_id, limit=10000)
    merged = event_log.query(db_path, event_type=EventType.INTENT_MERGED, tenant_id=tenant_id, limit=10000)
    rejected = event_log.query(db_path, event_type=EventType.INTENT_REJECTED, tenant_id=tenant_id, limit=10000)
    blocked = event_log.query(db_path, event_type=EventType.INTENT_BLOCKED, tenant_id=tenant_id, limit=10000)

    total_sims = len(sims)
    mergeable = sum(1 for s in sims if s["payload"].get("mergeable"))

    return {
        "total_simulations": total_sims,
        "mergeable": mergeable,
        "mergeable_rate": round(mergeable / total_sims, 3) if total_sims else 1.0,
        "total_merged": len(merged),
        "total_rejected": len(rejected),
        "total_blocked": len(blocked),
        "decision_distribution": {
            "merged": len(merged),
            "rejected": len(rejected),
            "blocked": len(blocked),
        },
        "tenant_id": tenant_id,
        "timestamp": now_iso(),
    }
