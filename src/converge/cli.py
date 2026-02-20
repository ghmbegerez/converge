"""CLI for Converge: grouped subcommands.

Commands:
  converge intent {create, list, status}
  converge simulate
  converge validate
  converge merge confirm
  converge queue {run, reset, inspect}
  converge policy {eval, calibrate}
  converge risk {eval, shadow, gate, review, policy}
  converge health {now, trend, change, change-trend, entropy, predict}
  converge compliance {report, alerts, threshold}
  converge agent {policy, authorize}
  converge audit {prune, events}
  converge export {decisions}
  converge metrics
  converge archaeology
  converge predictions
  converge serve
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from converge.models import (
    AgentPolicy,
    EventType,
    Intent,
    RiskLevel,
    Status,
    now_iso,
)


def _default_db() -> str:
    return str(Path(".converge") / "state.db")


def _out(data: Any) -> int:
    print(json.dumps(data, indent=2, default=str))
    if isinstance(data, dict) and "error" in data:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Intent commands
# ---------------------------------------------------------------------------

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
    )

    event_log.upsert_intent(args.db, intent)
    event_log.append(args.db, Event(
        event_type=EventType.INTENT_CREATED,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=intent.to_dict(),
    ))
    return _out({"ok": True, "intent_id": intent.id, "status": intent.status.value})


def cmd_intent_list(args: argparse.Namespace) -> int:
    from converge import event_log
    intents = event_log.list_intents(args.db, status=args.status, tenant_id=getattr(args, "tenant_id", None))
    return _out([i.to_dict() for i in intents])


def cmd_intent_status(args: argparse.Namespace) -> int:
    from converge import event_log
    from converge.models import Event
    intent = event_log.get_intent(args.db, args.intent_id)
    if intent is None:
        return _out({"error": f"Intent {args.intent_id} not found"})
    event_log.update_intent_status(args.db, args.intent_id, Status(args.status))
    event_log.append(args.db, Event(
        event_type=EventType.INTENT_STATUS_CHANGED,
        intent_id=args.intent_id,
        tenant_id=intent.tenant_id,
        payload={"from": intent.status.value, "to": args.status},
    ))
    return _out({"intent_id": args.intent_id, "status": args.status})


# ---------------------------------------------------------------------------
# Simulate
# ---------------------------------------------------------------------------

def cmd_simulate(args: argparse.Namespace) -> int:
    from converge import engine
    sim = engine.simulate(args.source, args.target, args.db,
                          intent_id=getattr(args, "intent_id", None))
    return _out({
        "mergeable": sim.mergeable,
        "conflicts": sim.conflicts,
        "files_changed": sim.files_changed,
        "source": sim.source,
        "target": sim.target,
        "timestamp": sim.timestamp,
    })


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    from converge import engine, event_log
    intent = event_log.get_intent(args.db, args.intent_id)
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
        event_log.upsert_intent(args.db, intent)
    result = engine.validate_intent(
        intent, args.db,
        use_last_simulation=args.use_last_simulation,
        skip_checks=args.skip_checks,
    )
    return _out(result)


# ---------------------------------------------------------------------------
# Merge confirm
# ---------------------------------------------------------------------------

def cmd_merge_confirm(args: argparse.Namespace) -> int:
    from converge import engine
    result = engine.confirm_merge(args.db, args.intent_id, getattr(args, "merged_commit", None))
    return _out(result)


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

def cmd_queue_run(args: argparse.Namespace) -> int:
    from converge import engine
    results = engine.process_queue(
        args.db,
        limit=args.limit,
        target=args.target,
        auto_confirm=args.auto_confirm,
        max_retries=args.max_retries,
        use_last_simulation=args.use_last_simulation,
        skip_checks=args.skip_checks,
    )
    return _out(results)


def cmd_queue_reset(args: argparse.Namespace) -> int:
    from converge import engine
    result = engine.reset_queue(args.db, args.intent_id,
                                 set_status=getattr(args, "set_status", None),
                                 clear_lock=args.clear_lock)
    return _out(result)


def cmd_queue_inspect(args: argparse.Namespace) -> int:
    from converge import engine
    result = engine.inspect_queue(
        args.db,
        status=getattr(args, "status", None),
        min_retries=getattr(args, "min_retries", None),
        only_actionable=args.only_actionable,
        limit=args.limit,
    )
    return _out(result)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def cmd_health_now(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.repo_health(args.db, tenant_id=getattr(args, "tenant_id", None)).to_dict())


def cmd_health_trend(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.health_trend(args.db, tenant_id=getattr(args, "tenant_id", None),
                                          days=args.days))


def cmd_health_change(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.change_health(args.db, args.intent_id,
                                           tenant_id=getattr(args, "tenant_id", None)))


def cmd_health_change_trend(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.change_health_trend(args.db, tenant_id=getattr(args, "tenant_id", None),
                                                 days=args.days))


def cmd_health_entropy(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.entropy_trend(args.db, tenant_id=getattr(args, "tenant_id", None),
                                           days=args.days))


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------

def cmd_compliance_report(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.compliance_report(args.db,
                tenant_id=getattr(args, "tenant_id", None)).to_dict())


def cmd_compliance_alerts(args: argparse.Namespace) -> int:
    from converge import projections
    report = projections.compliance_report(args.db, tenant_id=getattr(args, "tenant_id", None))
    result = _out({"alerts": report.alerts, "passed": report.passed})
    if args.fail_on_alert and report.alerts:
        return 3
    return result


def cmd_compliance_threshold_set(args: argparse.Namespace) -> int:
    from converge import event_log
    data = {}
    if args.min_mergeable_rate is not None:
        data["min_mergeable_rate"] = args.min_mergeable_rate
    if args.max_conflict_rate is not None:
        data["max_conflict_rate"] = args.max_conflict_rate
    if args.max_retries_total is not None:
        data["max_retries_total"] = args.max_retries_total
    if args.max_queue_tracked is not None:
        data["max_queue_tracked"] = args.max_queue_tracked
    event_log.upsert_compliance_thresholds(args.db, args.tenant_id, data)
    event_log.append(args.db, event_log.Event(
        event_type=EventType.COMPLIANCE_THRESHOLDS_UPDATED,
        tenant_id=args.tenant_id,
        payload=data,
    ))
    return _out({"ok": True, "tenant_id": args.tenant_id})


def cmd_compliance_threshold_get(args: argparse.Namespace) -> int:
    from converge import event_log
    data = event_log.get_compliance_thresholds(args.db, args.tenant_id)
    return _out(data or {"message": "No thresholds configured for this tenant"})


def cmd_compliance_threshold_list(args: argparse.Namespace) -> int:
    from converge import event_log
    return _out(event_log.list_compliance_thresholds(args.db))


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def cmd_agent_policy_set(args: argparse.Namespace) -> int:
    from converge import agents
    pol = AgentPolicy(
        agent_id=args.agent_id,
        tenant_id=getattr(args, "tenant_id", None),
        atl=args.atl if args.atl is not None else 0,
        max_risk_score=args.max_risk_score if args.max_risk_score is not None else 30.0,
        max_blast_severity=args.max_blast_severity or "low",
        require_human_approval=args.require_human_approval == "true" if args.require_human_approval else True,
        require_dual_approval_on_critical=args.require_dual_approval_on_critical == "true" if args.require_dual_approval_on_critical else True,
        allow_actions=args.allow_actions.split(",") if args.allow_actions else ["analyze"],
        action_overrides=json.loads(args.action_overrides_json) if args.action_overrides_json else {},
        expires_at=getattr(args, "expires_at", None),
    )
    return _out(agents.set_policy(args.db, pol))


def cmd_agent_policy_get(args: argparse.Namespace) -> int:
    from converge import agents
    pol = agents.get_policy(args.db, args.agent_id, getattr(args, "tenant_id", None))
    return _out(pol.to_dict())


def cmd_agent_policy_list(args: argparse.Namespace) -> int:
    from converge import agents
    return _out(agents.list_policies(args.db))


def cmd_agent_authorize(args: argparse.Namespace) -> int:
    from converge import agents
    result = agents.authorize(
        args.db,
        agent_id=args.agent_id,
        action=args.action,
        intent_id=args.intent_id,
        tenant_id=getattr(args, "tenant_id", None),
        human_approvals=args.human_approvals,
    )
    return _out(result)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def cmd_audit_prune(args: argparse.Namespace) -> int:
    from converge import event_log
    from datetime import datetime, timedelta, timezone
    before = (datetime.now(timezone.utc) - timedelta(days=args.retention_days)).isoformat()
    count = event_log.prune_events(args.db, before,
                                    tenant_id=getattr(args, "tenant_id", None),
                                    dry_run=args.dry_run)
    return _out({"pruned": count, "before": before, "dry_run": args.dry_run})


def cmd_audit_events(args: argparse.Namespace) -> int:
    from converge import event_log
    events = event_log.query(
        args.db,
        event_type=getattr(args, "type", None),
        intent_id=getattr(args, "intent_id", None),
        agent_id=getattr(args, "agent_id", None),
        tenant_id=getattr(args, "tenant_id", None),
        since=getattr(args, "since", None),
        limit=args.limit,
    )
    return _out(events)


# ---------------------------------------------------------------------------
# Metrics, archaeology, predictions
# ---------------------------------------------------------------------------

def cmd_metrics(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.integration_metrics(args.db, tenant_id=getattr(args, "tenant_id", None)))


def cmd_archaeology(args: argparse.Namespace) -> int:
    from converge import analytics
    report = analytics.archaeology_report(max_commits=args.max_commits, top=args.top)
    if getattr(args, "write_snapshot", None):
        analytics.save_archaeology_snapshot(report, args.write_snapshot)
    return _out(report)


def cmd_predictions(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.predict_issues(args.db, tenant_id=getattr(args, "tenant_id", None)))


def cmd_health_predict(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.predict_health(
        args.db,
        tenant_id=getattr(args, "tenant_id", None),
        horizon_days=args.horizon_days,
    ))


def cmd_export_decisions(args: argparse.Namespace) -> int:
    from converge import analytics
    return _out(analytics.export_decisions(
        args.db,
        output_path=getattr(args, "output", None),
        tenant_id=getattr(args, "tenant_id", None),
        fmt=args.format,
    ))


# ---------------------------------------------------------------------------
# Serve
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    from converge import server
    server.serve(args.db, host=args.host, port=args.port,
                 webhook_secret=getattr(args, "secret", ""))
    return 0


# ===================================================================
# Parser builder
# ===================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="converge", description="Code entropy control through semantic merge coordination")
    parser.add_argument("--db", default=_default_db(), help="SQLite database path")
    parser.add_argument("--actor", default="system", help="Actor identity for audit")
    sub = parser.add_subparsers(dest="command")

    # -- intent --
    intent_p = sub.add_parser("intent", help="Intent lifecycle")
    intent_sub = intent_p.add_subparsers(dest="intent_cmd")

    p = intent_sub.add_parser("create", help="Create intent from JSON file or branch")
    p.add_argument("--file", help="JSON file with intent definition")
    p.add_argument("--from-branch", help="Create intent directly from a branch name")
    p.add_argument("--target", help="Target branch (default: main)", default="main")
    p.add_argument("--intent-id", help="Custom intent ID")
    p.add_argument("--risk-level", choices=["low", "medium", "high", "critical"])
    p.add_argument("--priority", type=int)
    p.add_argument("--tenant-id")

    p = intent_sub.add_parser("list", help="List intents")
    p.add_argument("--status", choices=[s.value for s in Status])
    p.add_argument("--tenant-id")

    p = intent_sub.add_parser("status", help="Update intent status")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--status", required=True, choices=[s.value for s in Status])

    # -- simulate --
    p = sub.add_parser("simulate", help="Simulate merge in isolated worktree")
    p.add_argument("--source", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--intent-id")

    # -- validate --
    p = sub.add_parser("validate", help="Full validation: simulate + check + policy + risk")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--source")
    p.add_argument("--target")
    p.add_argument("--use-last-simulation", action="store_true")
    p.add_argument("--skip-checks", action="store_true")

    # -- merge --
    merge_p = sub.add_parser("merge", help="Merge operations")
    merge_sub = merge_p.add_subparsers(dest="merge_cmd")
    p = merge_sub.add_parser("confirm", help="Confirm merge for QUEUED intent")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--merged-commit")

    # -- queue --
    queue_p = sub.add_parser("queue", help="Queue operations")
    queue_sub = queue_p.add_subparsers(dest="queue_cmd")

    p = queue_sub.add_parser("run", help="Process merge queue")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--target", default="main")
    p.add_argument("--auto-confirm", action="store_true")
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--use-last-simulation", action="store_true")
    p.add_argument("--skip-checks", action="store_true")

    p = queue_sub.add_parser("reset", help="Reset queue retries for intent")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--set-status", choices=[s.value for s in Status])
    p.add_argument("--clear-lock", action="store_true")

    p = queue_sub.add_parser("inspect", help="Inspect queue state")
    p.add_argument("--status", choices=[s.value for s in Status])
    p.add_argument("--min-retries", type=int)
    p.add_argument("--only-actionable", action="store_true")
    p.add_argument("--limit", type=int, default=100)

    # -- policy --
    policy_p = sub.add_parser("policy", help="Policy operations")
    policy_sub = policy_p.add_subparsers(dest="policy_cmd")

    p = policy_sub.add_parser("eval", help="Evaluate policy without changing state")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--source")
    p.add_argument("--target")
    p.add_argument("--use-last-simulation", action="store_true")
    p.add_argument("--skip-checks", action="store_true")

    p = policy_sub.add_parser("calibrate", help="Calibrate profiles from history")
    p.add_argument("--output")

    # -- risk --
    risk_p = sub.add_parser("risk", help="Risk operations")
    risk_sub = risk_p.add_subparsers(dest="risk_cmd")

    p = risk_sub.add_parser("eval", help="Evaluate risk for intent")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--tenant-id")

    p = risk_sub.add_parser("shadow", help="Shadow risk evaluation (would-block)")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--tenant-id")

    p = risk_sub.add_parser("gate", help="Risk gate report")
    p.add_argument("--tenant-id")

    p = risk_sub.add_parser("review", help="Comprehensive risk review")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--tenant-id")

    p = risk_sub.add_parser("policy-set", help="Configure risk policy per tenant")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--max-risk-score", type=float)
    p.add_argument("--max-damage-score", type=float)
    p.add_argument("--max-propagation-score", type=float)
    p.add_argument("--mode", choices=["shadow", "enforce"])
    p.add_argument("--enforce-ratio", type=float)

    p = risk_sub.add_parser("policy-get", help="Get risk policy for tenant")
    p.add_argument("--tenant-id", required=True)

    # -- health --
    health_p = sub.add_parser("health", help="Health monitoring")
    health_sub = health_p.add_subparsers(dest="health_cmd")

    p = health_sub.add_parser("now", help="Current repo health")
    p.add_argument("--tenant-id")

    p = health_sub.add_parser("trend", help="Health trend over time")
    p.add_argument("--tenant-id")
    p.add_argument("--days", type=int, default=30)

    p = health_sub.add_parser("change", help="Health for a specific intent")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--tenant-id")

    p = health_sub.add_parser("change-trend", help="Change-level health trend")
    p.add_argument("--tenant-id")
    p.add_argument("--days", type=int, default=30)

    p = health_sub.add_parser("entropy", help="Entropy trend")
    p.add_argument("--tenant-id")
    p.add_argument("--days", type=int, default=30)

    p = health_sub.add_parser("predict", help="Predictive health projection")
    p.add_argument("--tenant-id")
    p.add_argument("--horizon-days", type=int, default=7)

    # -- compliance --
    comp_p = sub.add_parser("compliance", help="Compliance/SLO")
    comp_sub = comp_p.add_subparsers(dest="compliance_cmd")

    p = comp_sub.add_parser("report", help="Compliance report")
    p.add_argument("--tenant-id")

    p = comp_sub.add_parser("alerts", help="Compliance alerts")
    p.add_argument("--tenant-id")
    p.add_argument("--fail-on-alert", action="store_true")

    p = comp_sub.add_parser("threshold-set", help="Set compliance thresholds")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--min-mergeable-rate", type=float)
    p.add_argument("--max-conflict-rate", type=float)
    p.add_argument("--max-retries-total", type=int)
    p.add_argument("--max-queue-tracked", type=int)

    p = comp_sub.add_parser("threshold-get", help="Get compliance thresholds")
    p.add_argument("--tenant-id", required=True)

    p = comp_sub.add_parser("threshold-list", help="List all compliance thresholds")

    # -- agent --
    agent_p = sub.add_parser("agent", help="Agent authorization")
    agent_sub = agent_p.add_subparsers(dest="agent_cmd")

    p = agent_sub.add_parser("policy-set", help="Set agent policy")
    p.add_argument("--agent-id", required=True)
    p.add_argument("--tenant-id")
    p.add_argument("--atl", type=int, choices=[0, 1, 2, 3])
    p.add_argument("--max-risk-score", type=float)
    p.add_argument("--max-blast-severity", choices=["low", "medium", "high", "critical"])
    p.add_argument("--require-human-approval", choices=["true", "false"])
    p.add_argument("--require-dual-approval-on-critical", choices=["true", "false"])
    p.add_argument("--allow-actions")
    p.add_argument("--action-overrides-json")
    p.add_argument("--expires-at")

    p = agent_sub.add_parser("policy-get", help="Get agent policy")
    p.add_argument("--agent-id", required=True)
    p.add_argument("--tenant-id")

    p = agent_sub.add_parser("policy-list", help="List agent policies")

    p = agent_sub.add_parser("authorize", help="Authorize agent action")
    p.add_argument("--agent-id", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--intent-id", required=True)
    p.add_argument("--tenant-id")
    p.add_argument("--human-approvals", type=int, default=0)

    # -- audit --
    audit_p = sub.add_parser("audit", help="Audit operations")
    audit_sub = audit_p.add_subparsers(dest="audit_cmd")

    p = audit_sub.add_parser("prune", help="Prune old events")
    p.add_argument("--retention-days", type=int, default=90)
    p.add_argument("--tenant-id")
    p.add_argument("--dry-run", action="store_true")

    p = audit_sub.add_parser("events", help="Query event log")
    p.add_argument("--type")
    p.add_argument("--intent-id")
    p.add_argument("--agent-id")
    p.add_argument("--tenant-id")
    p.add_argument("--since")
    p.add_argument("--limit", type=int, default=100)

    # -- metrics --
    p = sub.add_parser("metrics", help="Integration metrics")
    p.add_argument("--tenant-id")

    # -- archaeology --
    p = sub.add_parser("archaeology", help="Git history analysis")
    p.add_argument("--max-commits", type=int, default=400)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--write-snapshot")

    # -- export --
    export_p = sub.add_parser("export", help="Export data")
    export_sub = export_p.add_subparsers(dest="export_cmd")

    p = export_sub.add_parser("decisions", help="Export decision dataset (JSONL/CSV)")
    p.add_argument("--output")
    p.add_argument("--tenant-id")
    p.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")

    # -- predictions --
    p = sub.add_parser("predictions", help="Predict issues from trends")
    p.add_argument("--tenant-id")

    # -- serve --
    p = sub.add_parser("serve", help="Start HTTP API server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9876)
    p.add_argument("--secret")

    return parser


# ===================================================================
# Dispatch
# ===================================================================

_DISPATCH = {
    ("intent", "create"): cmd_intent_create,
    ("intent", "list"): cmd_intent_list,
    ("intent", "status"): cmd_intent_status,
    ("simulate", None): cmd_simulate,
    ("validate", None): cmd_validate,
    ("merge", "confirm"): cmd_merge_confirm,
    ("queue", "run"): cmd_queue_run,
    ("queue", "reset"): cmd_queue_reset,
    ("queue", "inspect"): cmd_queue_inspect,
    ("policy", "eval"): cmd_policy_eval,
    ("policy", "calibrate"): cmd_policy_calibrate,
    ("risk", "eval"): cmd_risk_eval,
    ("risk", "shadow"): cmd_risk_shadow,
    ("risk", "gate"): cmd_risk_gate,
    ("risk", "review"): cmd_risk_review,
    ("risk", "policy-set"): cmd_risk_policy_set,
    ("risk", "policy-get"): cmd_risk_policy_get,
    ("health", "now"): cmd_health_now,
    ("health", "trend"): cmd_health_trend,
    ("health", "change"): cmd_health_change,
    ("health", "change-trend"): cmd_health_change_trend,
    ("health", "entropy"): cmd_health_entropy,
    ("compliance", "report"): cmd_compliance_report,
    ("compliance", "alerts"): cmd_compliance_alerts,
    ("compliance", "threshold-set"): cmd_compliance_threshold_set,
    ("compliance", "threshold-get"): cmd_compliance_threshold_get,
    ("compliance", "threshold-list"): cmd_compliance_threshold_list,
    ("agent", "policy-set"): cmd_agent_policy_set,
    ("agent", "policy-get"): cmd_agent_policy_get,
    ("agent", "policy-list"): cmd_agent_policy_list,
    ("agent", "authorize"): cmd_agent_authorize,
    ("audit", "prune"): cmd_audit_prune,
    ("audit", "events"): cmd_audit_events,
    ("metrics", None): cmd_metrics,
    ("archaeology", None): cmd_archaeology,
    ("predictions", None): cmd_predictions,
    ("export", "decisions"): cmd_export_decisions,
    ("health", "predict"): cmd_health_predict,
    ("serve", None): cmd_serve,
}

# Map subcmd attr names to the dispatch key
_SUBCMD_ATTR = {
    "intent": "intent_cmd",
    "merge": "merge_cmd",
    "queue": "queue_cmd",
    "policy": "policy_cmd",
    "risk": "risk_cmd",
    "health": "health_cmd",
    "compliance": "compliance_cmd",
    "agent": "agent_cmd",
    "audit": "audit_cmd",
    "export": "export_cmd",
}


def main(argv: list[str] | None = None) -> int:
    from converge import event_log as el

    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if not args.command:
        parser.print_help()
        return 1

    # Ensure DB exists
    el.init(args.db)

    # Resolve dispatch key
    subcmd_attr = _SUBCMD_ATTR.get(args.command)
    subcmd = getattr(args, subcmd_attr, None) if subcmd_attr else None
    key = (args.command, subcmd)

    handler = _DISPATCH.get(key)
    if handler is None:
        # Try command-only (no subcommand)
        handler = _DISPATCH.get((args.command, None))
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)
