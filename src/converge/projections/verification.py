"""Verification debt projection: quantifies accumulated technical debt.

Debt score (0-100) composed of 5 weighted factors:
  1. Staleness    (25): fraction of active intents older than threshold
  2. Queue depth  (20): active intents relative to capacity
  3. Review debt  (25): pending review tasks relative to threshold
  4. Conflict     (15): conflict rate from health signals
  5. Retry debt   (15): fraction of active intents with retries > 0
"""

from __future__ import annotations

from datetime import datetime, timezone

from converge import event_log
from converge.defaults import QUERY_LIMIT_LARGE
from converge.models import EventType, Status
from converge.projections._time import _since_hours
from converge.projections_models import DebtSnapshot

# ---------------------------------------------------------------------------
# Weight constants (sum = 100)
# ---------------------------------------------------------------------------

_W_STALENESS = 25.0
_W_QUEUE_PRESSURE = 20.0
_W_REVIEW_BACKLOG = 25.0
_W_CONFLICT = 15.0
_W_RETRY = 15.0

# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------

_STALE_HOURS = 24              # intent older than this is "stale"
_QUEUE_CAPACITY = 50           # active intents above this → full pressure
_REVIEW_CAPACITY = 10          # pending reviews above this → full pressure

# Status thresholds
_DEBT_GREEN = 30
_DEBT_YELLOW = 70


def _debt_status(score: float) -> str:
    if score <= _DEBT_GREEN:
        return "green"
    if score <= _DEBT_YELLOW:
        return "yellow"
    return "red"


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def verification_debt(
    tenant_id: str | None = None,
    *,
    stale_hours: int = _STALE_HOURS,
    queue_capacity: int = _QUEUE_CAPACITY,
    review_capacity: int = _REVIEW_CAPACITY,
) -> DebtSnapshot:
    """Compute verification debt from current system state."""

    # 1. Gather active intents
    intents = event_log.list_intents(tenant_id=tenant_id, limit=QUERY_LIMIT_LARGE)
    active = [i for i in intents if i.status in (Status.READY, Status.VALIDATED, Status.QUEUED)]
    active_count = len(active)

    # 2. Factor: staleness
    stale_cutoff = (datetime.now(timezone.utc)).isoformat()
    stale_count = 0
    if active:
        threshold_str = _since_hours(stale_hours)
        stale_count = sum(1 for i in active if i.created_at < threshold_str)

    staleness_ratio = (stale_count / active_count) if active_count > 0 else 0.0
    staleness_score = min(1.0, staleness_ratio) * _W_STALENESS

    # 3. Factor: queue depth pressure
    queue_ratio = min(1.0, active_count / queue_capacity) if queue_capacity > 0 else 0.0
    queue_pressure_score = queue_ratio * _W_QUEUE_PRESSURE

    # 4. Factor: review backlog
    pending_reviews = event_log.list_review_tasks(
        status="pending", tenant_id=tenant_id, limit=QUERY_LIMIT_LARGE,
    )
    assigned_reviews = event_log.list_review_tasks(
        status="assigned", tenant_id=tenant_id, limit=QUERY_LIMIT_LARGE,
    )
    review_count = len(pending_reviews) + len(assigned_reviews)
    review_ratio = min(1.0, review_count / review_capacity) if review_capacity > 0 else 0.0
    review_backlog_score = review_ratio * _W_REVIEW_BACKLOG

    # 5. Factor: conflict pressure (merge simulations + semantic conflicts)
    since_24h = _since_hours(24)
    sims = event_log.query(
        event_type=EventType.SIMULATION_COMPLETED,
        tenant_id=tenant_id, since=since_24h, limit=QUERY_LIMIT_LARGE,
    )
    total_sims = len(sims)
    conflict_count = sum(1 for s in sims if not s["payload"].get("mergeable"))
    merge_conflict_rate = (conflict_count / total_sims) if total_sims > 0 else 0.0

    # AR-22: Include semantic (inter-origin) conflicts in conflict pressure
    semantic_conflicts = event_log.query(
        event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
        tenant_id=tenant_id, since=since_24h, limit=QUERY_LIMIT_LARGE,
    )
    semantic_conflict_count = len(semantic_conflicts)
    # Normalize: 10+ open semantic conflicts → full semantic pressure
    semantic_rate = min(1.0, semantic_conflict_count / 10.0) if semantic_conflict_count > 0 else 0.0
    # Blend: 70% merge conflicts + 30% semantic conflicts
    conflict_rate = merge_conflict_rate * 0.7 + semantic_rate * 0.3
    conflict_pressure_score = conflict_rate * _W_CONFLICT

    # 6. Factor: retry pressure
    retry_count = sum(1 for i in active if i.retries > 0)
    retry_ratio = (retry_count / active_count) if active_count > 0 else 0.0
    retry_pressure_score = retry_ratio * _W_RETRY

    # Composite score
    debt_score = round(
        staleness_score + queue_pressure_score + review_backlog_score
        + conflict_pressure_score + retry_pressure_score,
        1,
    )

    snapshot = DebtSnapshot(
        debt_score=debt_score,
        staleness_score=round(staleness_score, 1),
        queue_pressure_score=round(queue_pressure_score, 1),
        review_backlog_score=round(review_backlog_score, 1),
        conflict_pressure_score=round(conflict_pressure_score, 1),
        retry_pressure_score=round(retry_pressure_score, 1),
        breakdown={
            "stale_intents": stale_count,
            "active_intents": active_count,
            "stale_hours_threshold": stale_hours,
            "queue_capacity": queue_capacity,
            "pending_reviews": review_count,
            "review_capacity": review_capacity,
            "conflict_rate": round(conflict_rate, 3),
            "retry_intents": retry_count,
        },
        status=_debt_status(debt_score),
        tenant_id=tenant_id,
    )

    # Emit snapshot event
    event_log.append(event_log.Event(
        event_type=EventType.VERIFICATION_DEBT_SNAPSHOT,
        tenant_id=tenant_id,
        payload=snapshot.to_dict(),
    ))

    return snapshot
