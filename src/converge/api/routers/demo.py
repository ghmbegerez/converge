"""Demo endpoints: quick-launch intent lifecycle and seed data."""

from __future__ import annotations

import os
import random
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from converge import event_log
from converge.api.auth import require_operator
from converge.models import Event, Intent, RiskLevel, Status, new_id, now_iso

router = APIRouter(tags=["demo"])


@router.post("/intents/demo-run")
def demo_run(
    body: dict[str, Any] | None = None,
    principal: dict = Depends(require_operator),
):
    """Create a demo intent and emit its full lifecycle events.

    Returns the created intent_id and shared trace_id so the caller
    can immediately query ``GET /intents/{id}/events`` for the timeline.
    """
    body = body or {}
    intent_id = f"demo-{new_id()}"
    trace_id = event_log.fresh_trace_id()
    ts = now_iso()

    source = body.get("source", "feature/demo-branch")
    target = body.get("target", "main")
    risk = random.choice(list(RiskLevel))

    # 1. Create the intent
    intent = Intent(
        id=intent_id,
        source=source,
        target=target,
        status=Status.READY,
        created_at=ts,
        created_by=principal.get("actor", "demo"),
        risk_level=risk,
        priority=random.randint(1, 5),
        semantic={"description": body.get("description", "Demo intent for proto evaluation")},
        origin_type="demo",
        tenant_id=principal.get("tenant"),
    )
    event_log.upsert_intent(intent)

    # 2. Emit lifecycle events with shared trace_id
    events = [
        Event(
            event_type="intent.created",
            intent_id=intent_id,
            trace_id=trace_id,
            payload={"source": source, "target": target, "risk_level": risk.value},
            tenant_id=principal.get("tenant"),
        ),
        Event(
            event_type="simulation.completed",
            intent_id=intent_id,
            trace_id=trace_id,
            payload={
                "mergeable": True,
                "conflicts": [],
                "files_changed": ["src/main.py", "tests/test_main.py"],
            },
            tenant_id=principal.get("tenant"),
        ),
        Event(
            event_type="risk.evaluated",
            intent_id=intent_id,
            trace_id=trace_id,
            payload={
                "risk_score": round(random.uniform(5, 80), 1),
                "risk_level": risk.value,
                "entropy_score": round(random.uniform(0.1, 2.0), 2),
                "containment_score": round(random.uniform(0.5, 1.0), 2),
            },
            tenant_id=principal.get("tenant"),
        ),
        Event(
            event_type="policy.evaluated",
            intent_id=intent_id,
            trace_id=trace_id,
            payload={
                "verdict": "ALLOW",
                "gates": [
                    {"gate": "verification", "passed": True},
                    {"gate": "containment", "passed": True},
                    {"gate": "entropy", "passed": True},
                ],
            },
            tenant_id=principal.get("tenant"),
        ),
    ]

    for ev in events:
        event_log.append(ev)

    return {
        "intent_id": intent_id,
        "trace_id": trace_id,
        "status": intent.status.value,
        "risk_level": risk.value,
        "events_emitted": len(events),
    }


@router.post("/demo/seed")
def demo_seed(
    body: dict[str, Any] | None = None,
    principal: dict = Depends(require_operator),
):
    """Bulk-create demo intents across lifecycle stages.

    Gated by CONVERGE_DEMO_MODE env var.
    """
    if os.environ.get("CONVERGE_DEMO_MODE", "0") != "1":
        raise HTTPException(
            status_code=403,
            detail="Demo seed requires CONVERGE_DEMO_MODE=1",
        )

    body = body or {}
    count = min(body.get("count", 10), 50)  # cap at 50
    tenant = principal.get("tenant")
    created = []

    stages = [
        (Status.READY, ["intent.created"]),
        (Status.VALIDATED, ["intent.created", "simulation.completed", "risk.evaluated", "policy.evaluated"]),
        (Status.QUEUED, ["intent.created", "simulation.completed", "risk.evaluated", "policy.evaluated", "intent.queued"]),
        (Status.MERGED, ["intent.created", "simulation.completed", "risk.evaluated", "policy.evaluated", "intent.queued", "intent.merged"]),
        (Status.REJECTED, ["intent.created", "simulation.completed", "risk.evaluated", "policy.evaluated", "intent.rejected"]),
    ]

    branches = [
        "feature/auth-refactor",
        "feature/payment-flow",
        "fix/null-pointer",
        "chore/deps-update",
        "feature/search-api",
        "fix/race-condition",
        "feature/dashboard-v2",
        "chore/ci-pipeline",
        "feature/notifications",
        "fix/memory-leak",
    ]

    for i in range(count):
        intent_id = f"seed-{new_id()}"
        trace_id = event_log.fresh_trace_id()
        status, event_types = stages[i % len(stages)]
        risk = random.choice(list(RiskLevel))
        branch = branches[i % len(branches)]

        intent = Intent(
            id=intent_id,
            source=branch,
            target="main",
            status=status,
            created_by="demo-seed",
            risk_level=risk,
            priority=random.randint(1, 5),
            semantic={"description": f"Seed intent {i + 1}: {branch}"},
            origin_type="demo",
            tenant_id=tenant,
        )
        event_log.upsert_intent(intent)

        for et in event_types:
            event_log.append(Event(
                event_type=et,
                intent_id=intent_id,
                trace_id=trace_id,
                payload={"seed": True, "stage": status.value},
                tenant_id=tenant,
            ))

        created.append({"intent_id": intent_id, "status": status.value})

    return {"seeded": len(created), "intents": created}
