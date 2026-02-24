"""CLI commands: intent lifecycle, simulate, validate."""

from __future__ import annotations

import argparse
import json

from converge.cli._helpers import _out
from converge.models import (
    EventType,
    Intent,
    RiskLevel,
    Status,
    now_iso,
)


def cmd_intent_create(args: argparse.Namespace) -> int:
    from converge import event_log
    from converge.models import Event, new_id

    # --from-branch shortcut: auto-generate intent from branch name
    from_branch = getattr(args, "from_branch", None)
    if from_branch:
        target = getattr(args, "target", None) or "main"
        intent_id = getattr(args, "intent_id", None) or f"branch-{new_id()}"
        data = {
            "id": intent_id,
            "source": from_branch,
            "target": target,
            "created_by": args.actor,
            "risk_level": getattr(args, "risk_level", None) or "medium",
            "priority": getattr(args, "priority", None) or 3,
            "tenant_id": getattr(args, "tenant_id", None),
        }
    elif getattr(args, "file", None):
        with open(args.file) as f:
            data = json.load(f)
    else:
        return _out({"error": "Either --file or --from-branch is required"})

    # Support both flat and nested formats
    intent_id = data.get("intent_id") or data.get("id")
    source = data.get("source") or data.get("technical", {}).get("source_ref", "")
    target = data.get("target") or data.get("technical", {}).get("target_ref", "main")

    origin = getattr(args, "origin_type", None) or data.get("origin_type", "human")
    intent = Intent(
        id=intent_id,
        source=source,
        target=target,
        status=Status(data.get("status", "READY")),
        created_at=data.get("created_at", now_iso()),
        created_by=data.get("created_by", args.actor),
        risk_level=RiskLevel(data.get("risk_level", "medium")),
        priority=data.get("priority", 3),
        semantic=data.get("semantic", {}),
        technical=data.get("technical", {}),
        checks_required=data.get("checks_required", []),
        dependencies=data.get("dependencies", []),
        tenant_id=data.get("tenant_id"),
        origin_type=origin,
    )

    # Intake pre-check: evaluate system health before accepting
    from converge.intake import evaluate_intake
    decision = evaluate_intake(intent)
    if not decision.accepted:
        return _out({
            "ok": False,
            "intent_id": intent.id,
            "rejected_by": "intake",
            "mode": decision.mode.value,
            "reason": decision.reason,
        })

    event_log.upsert_intent(intent)
    event_log.append(Event(
        event_type=EventType.INTENT_CREATED,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=intent.to_dict(),
    ))
    return _out({"ok": True, "intent_id": intent.id, "status": intent.status.value})


def cmd_intent_list(args: argparse.Namespace) -> int:
    from converge import event_log
    intents = event_log.list_intents(status=args.status, tenant_id=getattr(args, "tenant_id", None))
    return _out([i.to_dict() for i in intents])


def cmd_intent_status(args: argparse.Namespace) -> int:
    from converge import event_log
    from converge.models import Event
    intent = event_log.get_intent(args.intent_id)
    if intent is None:
        return _out({"error": f"Intent {args.intent_id} not found"})
    event_log.update_intent_status(args.intent_id, Status(args.status))
    event_log.append(Event(
        event_type=EventType.INTENT_STATUS_CHANGED,
        intent_id=args.intent_id,
        tenant_id=intent.tenant_id,
        payload={"from": intent.status.value, "to": args.status},
    ))
    return _out({"intent_id": args.intent_id, "status": args.status})


def cmd_simulate(args: argparse.Namespace) -> int:
    from converge import engine
    sim = engine.simulate(args.source, args.target,
                          intent_id=getattr(args, "intent_id", None))
    return _out({
        "mergeable": sim.mergeable,
        "conflicts": sim.conflicts,
        "files_changed": sim.files_changed,
        "source": sim.source,
        "target": sim.target,
        "timestamp": sim.timestamp,
    })


def cmd_validate(args: argparse.Namespace) -> int:
    from converge import engine, event_log
    intent = event_log.get_intent(args.intent_id)
    if intent is None:
        return _out({"error": f"Intent {args.intent_id} not found"})
    # Override source/target from args if provided
    modified = False
    if args.source:
        intent.source = args.source
        modified = True
    if args.target:
        intent.target = args.target
        modified = True
    if modified:
        event_log.upsert_intent(intent)
    result = engine.validate_intent(
        intent,
        use_last_simulation=args.use_last_simulation,
        skip_checks=args.skip_checks,
    )
    return _out(result)
