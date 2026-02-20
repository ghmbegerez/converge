"""CLI commands: risk evaluation, shadow, gate, review, policy."""

from __future__ import annotations

import argparse

from converge.cli._helpers import _out
from converge.models import EventType


def cmd_risk_eval(args: argparse.Namespace) -> int:
    from converge import event_log, risk as risk_mod
    from converge.models import Simulation
    intent = event_log.get_intent(args.db, args.intent_id)
    if intent is None:
        return _out({"error": f"Intent {args.intent_id} not found"})
    # Use last simulation if available
    sim_events = event_log.query(args.db, event_type=EventType.SIMULATION_COMPLETED, intent_id=args.intent_id, limit=1)
    if sim_events:
        p = sim_events[0]["payload"]
        sim = Simulation(mergeable=p["mergeable"], conflicts=p.get("conflicts", []),
                         files_changed=p.get("files_changed", []))
    else:
        sim = Simulation(mergeable=True)
    result = risk_mod.evaluate_risk(intent, sim)
    event_log.append(args.db, event_log.Event(
        event_type=EventType.RISK_EVALUATED,
        intent_id=args.intent_id,
        tenant_id=getattr(args, "tenant_id", None),
        payload=result.to_dict(),
    ))
    return _out(result.to_dict())


def cmd_risk_shadow(args: argparse.Namespace) -> int:
    from converge import event_log, policy as policy_mod
    risk_events = event_log.query(args.db, event_type=EventType.RISK_EVALUATED, intent_id=args.intent_id, limit=1)
    if not risk_events:
        return _out({"error": "No risk evaluation found. Run 'converge risk eval' first."})
    r = risk_events[0]["payload"]
    tenant = getattr(args, "tenant_id", None)
    thresholds = None
    if tenant:
        thresholds = event_log.get_risk_policy(args.db, tenant)
    result = policy_mod.evaluate_risk_gate(
        risk_score=r.get("risk_score", 0),
        damage_score=r.get("damage_score", 0),
        propagation_score=r.get("propagation_score", 0),
        thresholds=thresholds,
        mode="shadow",
    )
    result["intent_id"] = args.intent_id
    event_log.append(args.db, event_log.Event(
        event_type=EventType.RISK_SHADOW_EVALUATED,
        intent_id=args.intent_id,
        tenant_id=tenant,
        payload=result,
    ))
    return _out(result)


def cmd_risk_gate(args: argparse.Namespace) -> int:
    from converge import event_log
    events = event_log.query(args.db, event_type=EventType.POLICY_EVALUATED,
                             tenant_id=getattr(args, "tenant_id", None), limit=1000)
    blocked = [e for e in events if e["payload"].get("verdict") == "BLOCK"]
    return _out({
        "total_evaluations": len(events),
        "total_blocked": len(blocked),
        "block_rate": round(len(blocked) / max(len(events), 1), 3),
        "recent_blocks": blocked[:20],
    })


def cmd_risk_review(args: argparse.Namespace) -> int:
    from converge import analytics
    return _out(analytics.risk_review(args.db, args.intent_id,
                                       tenant_id=getattr(args, "tenant_id", None)))


def cmd_risk_policy_set(args: argparse.Namespace) -> int:
    from converge import event_log
    data = {}
    if args.max_risk_score is not None:
        data["max_risk_score"] = args.max_risk_score
    if args.max_damage_score is not None:
        data["max_damage_score"] = args.max_damage_score
    if args.max_propagation_score is not None:
        data["max_propagation_score"] = args.max_propagation_score
    if args.mode is not None:
        data["mode"] = args.mode
    if args.enforce_ratio is not None:
        data["enforce_ratio"] = args.enforce_ratio
    event_log.upsert_risk_policy(args.db, args.tenant_id, data)
    event_log.append(args.db, event_log.Event(
        event_type=EventType.RISK_POLICY_UPDATED,
        tenant_id=args.tenant_id,
        payload=data,
    ))
    return _out({"ok": True, "tenant_id": args.tenant_id})


def cmd_risk_policy_get(args: argparse.Namespace) -> int:
    from converge import event_log
    data = event_log.get_risk_policy(args.db, args.tenant_id)
    return _out(data or {"message": "No risk policy configured for this tenant"})


def cmd_policy_eval(args: argparse.Namespace) -> int:
    from converge import engine, event_log
    intent = event_log.get_intent(args.db, args.intent_id)
    if intent is None:
        return _out({"error": f"Intent {args.intent_id} not found"})
    modified = False
    if args.source:
        intent.source = args.source
        modified = True
    if args.target:
        intent.target = args.target
        modified = True
    if modified:
        event_log.upsert_intent(args.db, intent)
    result = engine.validate_intent(
        intent, args.db,
        use_last_simulation=args.use_last_simulation,
        skip_checks=args.skip_checks,
    )
    return _out(result)


def cmd_policy_calibrate(args: argparse.Namespace) -> int:
    from converge import analytics
    result = analytics.run_calibration(args.db, output_path=getattr(args, "output", None))
    return _out(result)
