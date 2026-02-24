"""Human review orchestration: creation, assignment, SLA, escalation.

Review tasks are created when policy evaluation or conflict detection
requires human judgment. Tasks track lifecycle from request through
assignment, review, and completion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from converge import event_log
from converge.defaults import QUERY_LIMIT_LARGE, REVIEW_SLA_HOURS
from converge.models import (
    Event,
    EventType,
    Intent,
    ReviewStatus,
    ReviewTask,
    RiskLevel,
    new_id,
    now_iso,
)


# ---------------------------------------------------------------------------
# SLA configuration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SLA computation
# ---------------------------------------------------------------------------

def _compute_sla_deadline(risk_level: RiskLevel, created_at: str | None = None) -> str:
    """Calculate SLA deadline based on risk level."""
    hours = REVIEW_SLA_HOURS.get(risk_level.value, 48)
    base = datetime.fromisoformat(created_at) if created_at else datetime.now(timezone.utc)
    deadline = base + timedelta(hours=hours)
    return deadline.isoformat()


# ---------------------------------------------------------------------------
# Create review task
# ---------------------------------------------------------------------------

def request_review(
    intent_id: str,
    *,
    trigger: str = "policy",
    reviewer: str | None = None,
    priority: int | None = None,
    tenant_id: str | None = None,
) -> ReviewTask:
    """Create a review task for an intent.

    Auto-computes SLA deadline from the intent's risk level.
    """
    intent = event_log.get_intent(intent_id)
    if intent is None:
        raise ValueError(f"Intent {intent_id} not found")

    task_id = f"rev-{new_id()}"
    created = now_iso()
    risk = intent.risk_level
    sla = _compute_sla_deadline(risk, created)

    task = ReviewTask(
        id=task_id,
        intent_id=intent_id,
        status=ReviewStatus.ASSIGNED if reviewer else ReviewStatus.PENDING,
        reviewer=reviewer,
        priority=priority if priority is not None else intent.priority,
        risk_level=risk,
        trigger=trigger,
        sla_deadline=sla,
        created_at=created,
        assigned_at=created if reviewer else None,
        tenant_id=tenant_id or intent.tenant_id,
    )
    event_log.upsert_review_task(task)

    event_log.append(Event(
        event_type=EventType.REVIEW_REQUESTED,
        intent_id=intent_id,
        tenant_id=task.tenant_id,
        payload=task.to_dict(),
    ))
    if reviewer:
        event_log.append(Event(
            event_type=EventType.REVIEW_ASSIGNED,
            intent_id=intent_id,
            tenant_id=task.tenant_id,
            payload={"task_id": task_id, "reviewer": reviewer},
        ))

    return task


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def assign_review(
    task_id: str,
    reviewer: str,
) -> ReviewTask:
    """Assign a review task to a reviewer."""
    task = event_log.get_review_task(task_id)
    if task is None:
        raise ValueError(f"Review task {task_id} not found")

    old_reviewer = task.reviewer
    assigned_at = now_iso()
    new_status = ReviewStatus.ASSIGNED

    event_log.update_review_task_status(
        task_id, new_status.value,
        reviewer=reviewer, assigned_at=assigned_at,
    )

    event_type = EventType.REVIEW_REASSIGNED if old_reviewer else EventType.REVIEW_ASSIGNED
    event_log.append(Event(
        event_type=event_type,
        intent_id=task.intent_id,
        tenant_id=task.tenant_id,
        payload={
            "task_id": task_id,
            "reviewer": reviewer,
            "old_reviewer": old_reviewer,
        },
    ))

    task.reviewer = reviewer
    task.assigned_at = assigned_at
    task.status = new_status
    return task


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def complete_review(
    task_id: str,
    *,
    resolution: str = "approved",
    notes: str = "",
) -> ReviewTask:
    """Complete a review task with a resolution."""
    task = event_log.get_review_task(task_id)
    if task is None:
        raise ValueError(f"Review task {task_id} not found")

    completed_at = now_iso()
    event_log.update_review_task_status(
        task_id, ReviewStatus.COMPLETED.value,
        completed_at=completed_at, resolution=resolution, notes=notes,
    )

    event_log.append(Event(
        event_type=EventType.REVIEW_COMPLETED,
        intent_id=task.intent_id,
        tenant_id=task.tenant_id,
        payload={
            "task_id": task_id,
            "reviewer": task.reviewer,
            "resolution": resolution,
            "notes": notes,
        },
    ))

    task.status = ReviewStatus.COMPLETED
    task.completed_at = completed_at
    task.resolution = resolution
    task.notes = notes
    return task


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def cancel_review(
    task_id: str,
    *,
    reason: str = "",
) -> ReviewTask:
    """Cancel a review task."""
    task = event_log.get_review_task(task_id)
    if task is None:
        raise ValueError(f"Review task {task_id} not found")

    event_log.update_review_task_status(
        task_id, ReviewStatus.CANCELLED.value,
        notes=reason,
    )

    event_log.append(Event(
        event_type=EventType.REVIEW_CANCELLED,
        intent_id=task.intent_id,
        tenant_id=task.tenant_id,
        payload={"task_id": task_id, "reason": reason},
    ))

    task.status = ReviewStatus.CANCELLED
    task.notes = reason
    return task


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

def escalate_review(
    task_id: str,
    *,
    reason: str = "sla_breach",
) -> ReviewTask:
    """Escalate a review task."""
    task = event_log.get_review_task(task_id)
    if task is None:
        raise ValueError(f"Review task {task_id} not found")

    escalated_at = now_iso()
    event_log.update_review_task_status(
        task_id, ReviewStatus.ESCALATED.value,
        escalated_at=escalated_at,
    )

    event_log.append(Event(
        event_type=EventType.REVIEW_ESCALATED,
        intent_id=task.intent_id,
        tenant_id=task.tenant_id,
        payload={
            "task_id": task_id,
            "reviewer": task.reviewer,
            "reason": reason,
        },
    ))

    task.status = ReviewStatus.ESCALATED
    task.escalated_at = escalated_at
    return task


# ---------------------------------------------------------------------------
# SLA breach detection
# ---------------------------------------------------------------------------

def check_sla_breaches(
    *,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Find review tasks that have breached their SLA deadline.

    Returns list of breached tasks with details. Emits events for each breach.
    """
    now = now_iso()
    breaches: list[dict[str, Any]] = []

    for status_val in (ReviewStatus.PENDING.value, ReviewStatus.ASSIGNED.value,
                       ReviewStatus.IN_REVIEW.value):
        tasks = event_log.list_review_tasks(
        status=status_val, tenant_id=tenant_id,
        )
        for task in tasks:
            if task.sla_deadline and task.sla_deadline < now:
                breach = {
                    "task_id": task.id,
                    "intent_id": task.intent_id,
                    "reviewer": task.reviewer,
                    "sla_deadline": task.sla_deadline,
                    "risk_level": task.risk_level.value,
                    "status": task.status.value,
                    "overdue_since": task.sla_deadline,
                }
                breaches.append(breach)

                event_log.append(Event(
                    event_type=EventType.REVIEW_SLA_BREACHED,
                    intent_id=task.intent_id,
                    tenant_id=task.tenant_id,
                    payload=breach,
                ))

    return breaches


# ---------------------------------------------------------------------------
# Review summary (for dashboard)
# ---------------------------------------------------------------------------

def review_summary(
    *,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Aggregate review task stats for dashboard."""
    all_tasks = event_log.list_review_tasks(
        tenant_id=tenant_id, limit=QUERY_LIMIT_LARGE,
    )

    by_status: dict[str, int] = {}
    by_reviewer: dict[str, int] = {}
    sla_breached = 0
    now = now_iso()

    for task in all_tasks:
        by_status[task.status.value] = by_status.get(task.status.value, 0) + 1
        if task.reviewer and task.status in (ReviewStatus.ASSIGNED, ReviewStatus.IN_REVIEW):
            by_reviewer[task.reviewer] = by_reviewer.get(task.reviewer, 0) + 1
        if (task.sla_deadline and task.sla_deadline < now
                and task.status in (ReviewStatus.PENDING, ReviewStatus.ASSIGNED, ReviewStatus.IN_REVIEW)):
            sla_breached += 1

    return {
        "total": len(all_tasks),
        "by_status": by_status,
        "by_reviewer": by_reviewer,
        "sla_breached": sla_breached,
        "timestamp": now,
    }
