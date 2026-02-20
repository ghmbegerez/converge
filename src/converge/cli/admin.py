"""CLI commands: serve, worker, health, compliance, agent, audit, export, metrics, archaeology, predictions."""

from __future__ import annotations

import argparse
import json

from converge.cli._helpers import _out
from converge.models import AgentPolicy, EventType


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


def cmd_health_predict(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.predict_health(
        args.db,
        tenant_id=getattr(args, "tenant_id", None),
        horizon_days=args.horizon_days,
    ))


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
# Metrics, archaeology, predictions, export
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


def cmd_export_decisions(args: argparse.Namespace) -> int:
    from converge import analytics
    return _out(analytics.export_decisions(
        args.db,
        output_path=getattr(args, "output", None),
        tenant_id=getattr(args, "tenant_id", None),
        fmt=args.format,
    ))


# ---------------------------------------------------------------------------
# Serve / Worker
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    from converge import server
    server.serve(args.db, host=args.host, port=args.port,
                 webhook_secret=getattr(args, "secret", ""))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from converge.worker import run_worker
    run_worker(db_path=args.db)
    return 0
