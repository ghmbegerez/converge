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
    return _out(projections.repo_health(tenant_id=getattr(args, "tenant_id", None)).to_dict())


def cmd_health_trend(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.health_trend(tenant_id=getattr(args, "tenant_id", None),
                                          days=args.days))


def cmd_health_change(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.change_health(args.intent_id,
                                           tenant_id=getattr(args, "tenant_id", None)))


def cmd_health_change_trend(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.change_health_trend(tenant_id=getattr(args, "tenant_id", None),
                                                 days=args.days))


def cmd_health_entropy(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.entropy_trend(tenant_id=getattr(args, "tenant_id", None),
                                           days=args.days))


def cmd_health_predict(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.predict_health(
        tenant_id=getattr(args, "tenant_id", None),
        horizon_days=args.horizon_days,
    ))


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------

def cmd_compliance_report(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.compliance_report(tenant_id=getattr(args, "tenant_id", None)).to_dict())


def cmd_compliance_alerts(args: argparse.Namespace) -> int:
    from converge import projections
    report = projections.compliance_report(tenant_id=getattr(args, "tenant_id", None))
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
    event_log.upsert_compliance_thresholds(args.tenant_id, data)
    event_log.append(event_log.Event(
        event_type=EventType.COMPLIANCE_THRESHOLDS_UPDATED,
        tenant_id=args.tenant_id,
        payload=data,
    ))
    return _out({"ok": True, "tenant_id": args.tenant_id})


def cmd_compliance_threshold_get(args: argparse.Namespace) -> int:
    from converge import event_log
    data = event_log.get_compliance_thresholds(args.tenant_id)
    return _out(data or {"message": "No thresholds configured for this tenant"})


def cmd_compliance_threshold_list(args: argparse.Namespace) -> int:
    from converge import event_log
    return _out(event_log.list_compliance_thresholds())


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
    return _out(agents.set_policy(pol))


def cmd_agent_policy_get(args: argparse.Namespace) -> int:
    from converge import agents
    pol = agents.get_policy(args.agent_id, getattr(args, "tenant_id", None))
    return _out(pol.to_dict())


def cmd_agent_policy_list(args: argparse.Namespace) -> int:
    from converge import agents
    return _out(agents.list_policies())


def cmd_agent_authorize(args: argparse.Namespace) -> int:
    from converge import agents
    result = agents.authorize(
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

def cmd_audit_init_chain(args: argparse.Namespace) -> int:
    from converge import audit_chain
    return _out(audit_chain.initialize_chain())


def cmd_audit_verify_chain(args: argparse.Namespace) -> int:
    from converge import audit_chain
    result = audit_chain.verify_chain()
    _out(result)
    return 0 if result.get("valid") else 3


def cmd_audit_prune(args: argparse.Namespace) -> int:
    from converge import event_log
    from datetime import datetime, timedelta, timezone
    before = (datetime.now(timezone.utc) - timedelta(days=args.retention_days)).isoformat()
    count = event_log.prune_events(before,
                                    tenant_id=getattr(args, "tenant_id", None),
                                    dry_run=args.dry_run)
    return _out({"pruned": count, "before": before, "dry_run": args.dry_run})


def cmd_audit_events(args: argparse.Namespace) -> int:
    from converge import event_log
    events = event_log.query(
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
    return _out(projections.integration_metrics(tenant_id=getattr(args, "tenant_id", None)))


def cmd_archaeology(args: argparse.Namespace) -> int:
    sub = getattr(args, "archaeology_cmd", None)
    if sub == "refresh":
        return cmd_archaeology_refresh(args)
    # Default: report (backward compat when no subcommand or "report")
    from converge import analytics
    report = analytics.archaeology_report(
        max_commits=getattr(args, "max_commits", 400),
        top=getattr(args, "top", 20),
    )
    if getattr(args, "write_snapshot", None):
        analytics.save_archaeology_snapshot(report, args.write_snapshot)
    return _out(report)


def cmd_archaeology_refresh(args: argparse.Namespace) -> int:
    from converge import analytics
    result = analytics.refresh_snapshot(
        max_commits=getattr(args, "max_commits", 400),
        output_path=getattr(args, "output", None),
    )
    if not result.get("valid", False):
        _out(result)
        return 1
    return _out(result)


def cmd_review_request(args: argparse.Namespace) -> int:
    from converge import reviews
    return _out(reviews.request_review(
        args.intent_id, trigger=getattr(args, "trigger", "manual"),
        reviewer=getattr(args, "reviewer", None), priority=getattr(args, "priority", None),
        tenant_id=getattr(args, "tenant_id", None)).to_dict())

def cmd_review_list(args: argparse.Namespace) -> int:
    from converge import event_log
    tasks = event_log.list_review_tasks(
        intent_id=getattr(args, "intent_id", None), status=getattr(args, "status", None),
        reviewer=getattr(args, "reviewer", None), tenant_id=getattr(args, "tenant_id", None),
        limit=getattr(args, "limit", 50))
    return _out([t.to_dict() for t in tasks])

def cmd_review_assign(args: argparse.Namespace) -> int:
    from converge import reviews
    return _out(reviews.assign_review(args.task_id, args.reviewer).to_dict())

def cmd_review_complete(args: argparse.Namespace) -> int:
    from converge import reviews
    return _out(reviews.complete_review(
        args.task_id, resolution=args.resolution, notes=getattr(args, "notes", "")).to_dict())

def cmd_review_cancel(args: argparse.Namespace) -> int:
    from converge import reviews
    return _out(reviews.cancel_review(args.task_id, reason=getattr(args, "reason", "")).to_dict())

def cmd_review_escalate(args: argparse.Namespace) -> int:
    from converge import reviews
    return _out(reviews.escalate_review(
        args.task_id, reason=getattr(args, "reason", "manual_escalation")).to_dict())

def cmd_review_sla_check(args: argparse.Namespace) -> int:
    from converge import reviews
    breaches = reviews.check_sla_breaches(tenant_id=getattr(args, "tenant_id", None))
    return _out({"breaches": breaches, "count": len(breaches)})

def cmd_review_summary(args: argparse.Namespace) -> int:
    from converge import reviews
    return _out(reviews.review_summary(tenant_id=getattr(args, "tenant_id", None)))


def cmd_semantic_status(args: argparse.Namespace) -> int:
    from converge import event_log
    return _out(event_log.embedding_coverage(
        tenant_id=getattr(args, "tenant_id", None),
        model=getattr(args, "model", None),
    ))


def cmd_semantic_index(args: argparse.Namespace) -> int:
    from converge.semantic.indexer import index_intent
    from converge.semantic.embeddings import get_provider
    provider = get_provider(getattr(args, "provider", "deterministic"))
    result = index_intent(
        args.intent_id, provider,
        force=getattr(args, "force", False),
    )
    return _out(result)


def cmd_semantic_reindex(args: argparse.Namespace) -> int:
    from converge.semantic.indexer import reindex
    result = reindex(
        provider_name=getattr(args, "provider", "deterministic"),
        tenant_id=getattr(args, "tenant_id", None),
        force=getattr(args, "force", False),
        dry_run=getattr(args, "dry_run", False),
    )
    if not result.get("total", 0):
        _out(result)
        return 1
    return _out(result)


def cmd_semantic_conflicts(args: argparse.Namespace) -> int:
    from converge.semantic.conflicts import scan_conflicts
    report = scan_conflicts(
        model=getattr(args, "model", "deterministic-v1"),
        tenant_id=getattr(args, "tenant_id", None),
        target=getattr(args, "target", None),
        similarity_threshold=getattr(args, "similarity_threshold", 0.70),
        conflict_threshold=getattr(args, "conflict_threshold", 0.60),
        mode=getattr(args, "mode", "shadow"),
    )
    return _out({
        "conflicts": [
            {
                "intent_a": c.intent_a,
                "intent_b": c.intent_b,
                "score": c.score,
                "similarity": c.similarity,
                "target": c.target,
            }
            for c in report.conflicts
        ],
        "candidates_checked": report.candidates_checked,
        "mode": report.mode,
        "threshold": report.threshold,
        "timestamp": report.timestamp,
    })


def cmd_semantic_conflict_list(args: argparse.Namespace) -> int:
    from converge.semantic.conflicts import list_conflicts
    return _out(list_conflicts(
        tenant_id=getattr(args, "tenant_id", None),
        limit=getattr(args, "limit", 50),
    ))


def cmd_semantic_conflict_resolve(args: argparse.Namespace) -> int:
    from converge.semantic.conflicts import resolve_conflict
    return _out(resolve_conflict(
        args.intent_a,
        args.intent_b,
        resolution=getattr(args, "resolution", "acknowledged"),
        resolved_by=args.actor,
        tenant_id=getattr(args, "tenant_id", None),
    ))


def cmd_predictions(args: argparse.Namespace) -> int:
    from converge import projections
    return _out(projections.predict_issues(tenant_id=getattr(args, "tenant_id", None)))


def cmd_export_decisions(args: argparse.Namespace) -> int:
    from converge import exports
    return _out(exports.export_decisions(
        output_path=getattr(args, "output", None),
        tenant_id=getattr(args, "tenant_id", None),
        fmt=args.format,
    ))


# ---------------------------------------------------------------------------
# Verification debt
# ---------------------------------------------------------------------------

def cmd_verification_debt(args: argparse.Namespace) -> int:
    from converge import projections
    debt = projections.verification_debt(tenant_id=getattr(args, "tenant_id", None))
    return _out(debt.to_dict())


# ---------------------------------------------------------------------------
# Intake control
# ---------------------------------------------------------------------------

def cmd_intake_status(args: argparse.Namespace) -> int:
    from converge import intake
    return _out(intake.intake_status(tenant_id=getattr(args, "tenant_id", None)))


def cmd_intake_set_mode(args: argparse.Namespace) -> int:
    from converge import intake
    return _out(intake.set_intake_mode(
        args.mode,
        tenant_id=getattr(args, "tenant_id", None),
        set_by=args.actor,
        reason=getattr(args, "reason", ""),
    ))


# ---------------------------------------------------------------------------
# Pre-evaluation harness
# ---------------------------------------------------------------------------

def cmd_harness_evaluate(args: argparse.Namespace) -> int:
    from converge import harness
    intent_data = json.loads(open(args.file).read())
    cfg = harness.HarnessConfig(mode=getattr(args, "mode", "shadow"))
    result = harness.evaluate_intent(intent_data, config=cfg)
    return _out(result.to_dict())


# ---------------------------------------------------------------------------
# Security scanning
# ---------------------------------------------------------------------------

def cmd_security_scan(args: argparse.Namespace) -> int:
    from converge import security
    return _out(security.run_scan(
        args.path,
        intent_id=getattr(args, "intent_id", None),
        tenant_id=getattr(args, "tenant_id", None),
    ))


def cmd_security_findings(args: argparse.Namespace) -> int:
    from converge import event_log
    findings = event_log.list_security_findings(
        intent_id=getattr(args, "intent_id", None),
        scanner=getattr(args, "scanner", None),
        severity=getattr(args, "severity", None),
        category=getattr(args, "category", None),
        tenant_id=getattr(args, "tenant_id", None),
        limit=getattr(args, "limit", 100),
    )
    return _out({"findings": findings, "total": len(findings)})


def cmd_security_summary(args: argparse.Namespace) -> int:
    from converge import security
    return _out(security.scan_summary(
        tenant_id=getattr(args, "tenant_id", None),
    ))


# ---------------------------------------------------------------------------
# Coherence harness
# ---------------------------------------------------------------------------

def cmd_coherence_init(args: argparse.Namespace) -> int:
    from converge import coherence
    return _out(coherence.init_harness())


def cmd_coherence_list(args: argparse.Namespace) -> int:
    from converge import coherence
    return _out(coherence.list_questions(path=getattr(args, "path", None)))


def cmd_coherence_run(args: argparse.Namespace) -> int:
    from converge import coherence
    questions = coherence.load_questions(path=getattr(args, "path", None))
    if not questions:
        return _out({"status": "no_questions", "message": "No coherence harness configured"})
    result = coherence.evaluate(questions)
    return _out(result.to_dict())


def cmd_coherence_baseline(args: argparse.Namespace) -> int:
    from converge import coherence
    questions = coherence.load_questions()
    if not questions:
        return _out({"status": "no_questions", "message": "No coherence harness configured"})
    result = coherence.evaluate(questions)
    baselines = coherence.update_baselines(result.results)
    return _out({"status": "updated", "baselines": baselines})


def cmd_coherence_suggest(args: argparse.Namespace) -> int:
    from converge import coherence_feedback
    from converge.feature_flags import is_enabled
    if not is_enabled("coherence_feedback"):
        return _out({"status": "disabled", "message": "coherence_feedback flag is disabled"})
    suggestions = coherence_feedback.analyze_patterns(
        lookback_days=getattr(args, "lookback_days", 90),
    )
    count = coherence_feedback.emit_suggestions(suggestions)
    return _out({"suggestions": suggestions, "emitted": count})


def cmd_coherence_accept(args: argparse.Namespace) -> int:
    from converge import coherence_feedback
    result = coherence_feedback.accept_suggestion(args.suggestion_id)
    if result is None:
        return _out({"status": "not_found", "suggestion_id": args.suggestion_id})
    return _out({"status": "accepted", "suggestion": result})


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    """Validate environment setup and report health."""
    import shutil
    from converge import event_log, feature_flags

    checks, overall = [], "pass"

    def _add(name: str, status: str, detail: str | dict) -> None:
        nonlocal overall
        checks.append({"check": name, "status": status, "detail": detail})
        if status == "fail":
            overall = "fail"
        elif status == "warn" and overall == "pass":
            overall = "warn"

    try:
        event_log.query(limit=1)
        _add("database", "pass", str(args.db))
    except Exception as e:
        _add("database", "fail", str(e))

    try:
        from converge import scm
        _add("git_repo", "pass", str(scm.repo_root()))
    except Exception:
        _add("git_repo", "warn", "Not inside a git repository")

    try:
        event_log.list_intents(limit=1)
        _add("schema", "pass", "intents table accessible")
    except Exception as e:
        _add("schema", "fail", str(e))

    try:
        flags = feature_flags.list_flags()
        enabled = sum(1 for f in flags if f.get("enabled"))
        _add("feature_flags", "pass", f"{enabled}/{len(flags)} enabled")
    except Exception as e:
        _add("feature_flags", "warn", str(e))

    tools = {t: shutil.which(t) is not None for t in ("bandit", "gitleaks", "pip-audit")}
    _add("security_tools", "pass" if any(tools.values()) else "warn",
         {n: ("found" if v else "missing") for n, v in tools.items()})

    return _out({"overall": overall, "checks": checks})


# ---------------------------------------------------------------------------
# Serve / Worker
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    from converge import server
    server.serve(host=args.host, port=args.port,
                 webhook_secret=getattr(args, "secret", ""))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from converge.worker import run_worker
    run_worker()
    return 0
