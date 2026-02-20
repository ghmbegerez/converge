"""Compliance projections: SLO/KPI evaluation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from converge import event_log
from converge.models import ComplianceReport, EventType


DEFAULT_THRESHOLDS = {
    "min_mergeable_rate": 0.80,
    "max_conflict_rate": 0.20,
    "max_retries_total": 200,
    "max_queue_tracked": 1000,
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

    sims = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, tenant_id=tenant_id, limit=10000)
    total = len(sims)
    mergeable = sum(1 for s in sims if s["payload"].get("mergeable"))
    mergeable_rate = (mergeable / total) if total > 0 else 1.0
    conflict_rate = 1.0 - mergeable_rate

    queue_events = event_log.query(db_path, event_type=EventType.QUEUE_RESET, tenant_id=tenant_id, limit=10000)
    requeue_events = event_log.query(db_path, event_type=EventType.INTENT_REQUEUED, tenant_id=tenant_id, limit=10000)
    retries_total = len(queue_events) + len(requeue_events)

    intents = event_log.list_intents(db_path, tenant_id=tenant_id, limit=10000)
    queue_tracked = len(intents)

    checks = []
    alerts = []

    def _check(name: str, value: float, op: str, threshold: float):
        passed = (value >= threshold) if op == ">=" else (value <= threshold)
        entry = {"name": name, "value": value, "threshold": threshold, "op": op, "passed": passed}
        checks.append(entry)
        if not passed:
            alerts.append({"alert": f"SLO breach: {name}", **entry})

    _check("mergeable_rate", round(mergeable_rate, 3), ">=", t.get("min_mergeable_rate", 0.80))
    _check("conflict_rate", round(conflict_rate, 3), "<=", t.get("max_conflict_rate", 0.20))
    _check("retries_total", retries_total, "<=", t.get("max_retries_total", 200))
    _check("queue_tracked", queue_tracked, "<=", t.get("max_queue_tracked", 1000))

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
