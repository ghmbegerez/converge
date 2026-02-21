"""Compliance projections: SLO/KPI evaluation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from converge import event_log
from converge.models import ComplianceReport, EventType

# --- SLO threshold defaults ---
_MIN_MERGEABLE_RATE = 0.80
_MAX_CONFLICT_RATE = 0.20
_MAX_RETRIES_TOTAL = 200
_MAX_QUEUE_TRACKED = 1000
_QUERY_LIMIT = 10000

DEFAULT_THRESHOLDS = {
    "min_mergeable_rate": _MIN_MERGEABLE_RATE,
    "max_conflict_rate": _MAX_CONFLICT_RATE,
    "max_retries_total": _MAX_RETRIES_TOTAL,
    "max_queue_tracked": _MAX_QUEUE_TRACKED,
}


def compliance_report(
    db_path: str | Path,
    tenant_id: str | None = None,
    thresholds: dict[str, Any] | None = None,
) -> ComplianceReport:
    """Evaluate SLO/KPIs from event history."""
    t = thresholds or DEFAULT_THRESHOLDS

    # Load tenant-specific thresholds if available
    if tenant_id:
        stored = event_log.get_compliance_thresholds(db_path, tenant_id)
        if stored:
            t = {**DEFAULT_THRESHOLDS, **stored}

    sims = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, tenant_id=tenant_id, limit=_QUERY_LIMIT)
    total = len(sims)
    mergeable = sum(1 for s in sims if s["payload"].get("mergeable"))
    mergeable_rate = (mergeable / total) if total > 0 else 1.0
    conflict_rate = 1.0 - mergeable_rate

    queue_events = event_log.query(db_path, event_type=EventType.QUEUE_RESET, tenant_id=tenant_id, limit=_QUERY_LIMIT)
    requeue_events = event_log.query(db_path, event_type=EventType.INTENT_REQUEUED, tenant_id=tenant_id, limit=_QUERY_LIMIT)
    retries_total = len(queue_events) + len(requeue_events)

    intents = event_log.list_intents(db_path, tenant_id=tenant_id, limit=_QUERY_LIMIT)
    queue_tracked = len(intents)

    checks = []
    alerts = []

    def _check(name: str, value: float, op: str, threshold: float):
        passed = (value >= threshold) if op == ">=" else (value <= threshold)
        entry = {"name": name, "value": value, "threshold": threshold, "op": op, "passed": passed}
        checks.append(entry)
        if not passed:
            alerts.append({"alert": f"SLO breach: {name}", **entry})

    _check("mergeable_rate", round(mergeable_rate, 3), ">=", t.get("min_mergeable_rate", _MIN_MERGEABLE_RATE))
    _check("conflict_rate", round(conflict_rate, 3), "<=", t.get("max_conflict_rate", _MAX_CONFLICT_RATE))
    _check("retries_total", retries_total, "<=", t.get("max_retries_total", _MAX_RETRIES_TOTAL))
    _check("queue_tracked", queue_tracked, "<=", t.get("max_queue_tracked", _MAX_QUEUE_TRACKED))

    return ComplianceReport(
        mergeable_rate=round(mergeable_rate, 3),
        conflict_rate=round(conflict_rate, 3),
        retries_total=retries_total,
        queue_tracked=queue_tracked,
        checks=checks,
        passed=all(c["passed"] for c in checks),
        alerts=alerts,
        tenant_id=tenant_id,
    )
