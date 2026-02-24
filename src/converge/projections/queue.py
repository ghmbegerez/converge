"""Queue state and agent performance projections."""

from __future__ import annotations

from typing import Any

from converge import event_log
from converge.defaults import QUERY_LIMIT_LARGE
from converge.models import Status
from converge.projections_models import QueueState


def queue_state(tenant_id: str | None = None) -> QueueState:
    """Current queue state derived from intents table."""
    intents = event_log.list_intents(tenant_id=tenant_id, limit=QUERY_LIMIT_LARGE)
    by_status: dict[str, int] = {}
    pending = []
    for i in intents:
        by_status[i.status.value] = by_status.get(i.status.value, 0) + 1
        if i.status in (Status.READY, Status.VALIDATED, Status.QUEUED):
            pending.append({
                "intent_id": i.id, "status": i.status.value,
                "priority": i.priority, "retries": i.retries,
            })
    pending.sort(key=lambda x: (x["priority"], x["intent_id"]))
    return QueueState(pending=pending, total=len(intents), by_status=by_status)


def agent_performance(
    agent_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Compute agent trust metrics from event history."""
    agent_events = event_log.query(agent_id=agent_id, tenant_id=tenant_id, limit=QUERY_LIMIT_LARGE)
    total = len(agent_events)
    by_type: dict[str, int] = {}
    for e in agent_events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1

    merged = by_type.get("intent.merged", 0)
    rejected = by_type.get("intent.rejected", 0)
    blocked = by_type.get("intent.blocked", 0)
    success_rate = (merged / (merged + rejected + blocked)) if (merged + rejected + blocked) > 0 else 0.0

    return {
        "agent_id": agent_id,
        "total_events": total,
        "merged": merged,
        "rejected": rejected,
        "blocked": blocked,
        "success_rate": round(success_rate, 3),
        "events_by_type": by_type,
        "trust_score": round(min(100.0, success_rate * 100 + min(merged, 50)), 1),
        "tenant_id": tenant_id,
    }
