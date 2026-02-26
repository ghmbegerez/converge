"""Argparse parser definition for Converge CLI."""

from __future__ import annotations

import argparse

from converge.cli._helpers import _default_db
from converge.defaults import DEFAULT_TARGET_BRANCH
from converge.models import Status


def build_parser(*, show_all: bool = True) -> argparse.ArgumentParser:
    epilog = None if show_all else (
        "\nUse 'converge --help-all' to see all available commands "
        "(risk, health, compliance, agent, audit, semantic, review, intake, security, export)."
    )
    parser = argparse.ArgumentParser(
        prog="converge",
        description="Code entropy control through semantic merge coordination",
        epilog=epilog,
    )
    parser.add_argument("--db", default=_default_db(), help="SQLite database path")
    parser.add_argument("--actor", default="system", help="Actor identity for audit")
    sub = parser.add_subparsers(dest="command")

    # Essential commands (always visible)
    _register_intent_commands(sub)
    _register_queue_commands(sub)
    _register_server_commands(sub)

    # Doctor command (always visible)
    sub.add_parser("doctor", help="Validate environment setup and report health")

    # Advanced commands (visible with --help-all)
    if show_all:
        _register_risk_commands(sub)
        _register_health_commands(sub)
        _register_agent_commands(sub)
        _register_audit_commands(sub)
        _register_semantic_commands(sub)
        _register_review_commands(sub)
        _register_intake_commands(sub)
        _register_export_commands(sub)
        _register_coherence_commands(sub)

    return parser


def _register_intent_commands(sub: argparse._SubParsersAction) -> None:
    # -- intent --
    intent_p = sub.add_parser("intent", help="Intent lifecycle")
    intent_sub = intent_p.add_subparsers(dest="intent_cmd")

    p = intent_sub.add_parser("create", help="Create intent from JSON file or branch")
    p.add_argument("--file", help="JSON file with intent definition")
    p.add_argument("--from-branch", help="Create intent directly from a branch name")
    p.add_argument("--target", help="Target branch (default: main)", default=DEFAULT_TARGET_BRANCH)
    p.add_argument("--intent-id", help="Custom intent ID")
    p.add_argument("--risk-level", choices=["low", "medium", "high", "critical"])
    p.add_argument("--priority", type=int)
    p.add_argument("--tenant-id")
    p.add_argument("--origin-type", choices=["human", "agent", "integration"], default="human")

    p = intent_sub.add_parser("list", help="List intents")
    p.add_argument("--status", choices=[s.value for s in Status])
    p.add_argument("--tenant-id")

    p = intent_sub.add_parser("status", help="Update intent status")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--status", required=True, choices=[s.value for s in Status])

    # -- simulate --
    p = sub.add_parser("simulate", help="Simulate merge via git merge-tree (no worktree mutation)")
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


def _register_queue_commands(sub: argparse._SubParsersAction) -> None:
    # -- queue --
    queue_p = sub.add_parser("queue", help="Queue operations")
    queue_sub = queue_p.add_subparsers(dest="queue_cmd")

    p = queue_sub.add_parser("run", help="Process merge queue")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--target", default=DEFAULT_TARGET_BRANCH)
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


def _register_risk_commands(sub: argparse._SubParsersAction) -> None:
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


def _register_health_commands(sub: argparse._SubParsersAction) -> None:
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

    # -- verification --
    ver_p = sub.add_parser("verification", help="Verification debt")
    ver_sub = ver_p.add_subparsers(dest="verification_cmd")

    p = ver_sub.add_parser("debt", help="Current verification debt score")
    p.add_argument("--tenant-id")

    # -- predictions --
    p = sub.add_parser("predictions", help="Predict issues from trends")
    p.add_argument("--tenant-id")


def _register_agent_commands(sub: argparse._SubParsersAction) -> None:
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


def _register_audit_commands(sub: argparse._SubParsersAction) -> None:
    # -- audit --
    audit_p = sub.add_parser("audit", help="Audit operations")
    audit_sub = audit_p.add_subparsers(dest="audit_cmd")

    p = audit_sub.add_parser("prune", help="Prune old events")
    p.add_argument("--retention-days", type=int, default=90)
    p.add_argument("--tenant-id")
    p.add_argument("--dry-run", action="store_true")

    p = audit_sub.add_parser("init-chain", help="Initialize event tamper-evidence chain")

    p = audit_sub.add_parser("verify-chain", help="Verify event chain integrity")

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
    arch_p = sub.add_parser("archaeology", help="Git history analysis")
    arch_sub = arch_p.add_subparsers(dest="archaeology_cmd")

    p = arch_sub.add_parser("report", help="Run archaeology report")
    p.add_argument("--max-commits", type=int, default=400)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--write-snapshot")

    p = arch_sub.add_parser("refresh", help="Refresh snapshot and validate")
    p.add_argument("--max-commits", type=int, default=400)
    p.add_argument("--output")


def _register_semantic_commands(sub: argparse._SubParsersAction) -> None:
    # -- semantic --
    sem_p = sub.add_parser("semantic", help="Semantic processing")
    sem_sub = sem_p.add_subparsers(dest="semantic_cmd")

    p = sem_sub.add_parser("status", help="Embedding coverage status")
    p.add_argument("--tenant-id")
    p.add_argument("--model")

    p = sem_sub.add_parser("index", help="Index a single intent")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--provider", default="deterministic")
    p.add_argument("--force", action="store_true")

    p = sem_sub.add_parser("reindex", help="Reindex all embeddings")
    p.add_argument("--tenant-id")
    p.add_argument("--provider", default="deterministic")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    p = sem_sub.add_parser("conflicts", help="Scan for semantic conflicts")
    p.add_argument("--tenant-id")
    p.add_argument("--target", help="Filter by target branch")
    p.add_argument("--model", default="deterministic-v1")
    p.add_argument("--similarity-threshold", type=float, default=0.70)
    p.add_argument("--conflict-threshold", type=float, default=0.60)
    p.add_argument("--mode", choices=["shadow", "enforce"], default="shadow")

    p = sem_sub.add_parser("conflict-list", help="List active (unresolved) conflicts")
    p.add_argument("--tenant-id")
    p.add_argument("--limit", type=int, default=50)

    p = sem_sub.add_parser("conflict-resolve", help="Resolve a conflict pair")
    p.add_argument("--intent-a", required=True)
    p.add_argument("--intent-b", required=True)
    p.add_argument("--resolution", default="acknowledged")
    p.add_argument("--tenant-id")


def _register_review_commands(sub: argparse._SubParsersAction) -> None:
    # -- review --
    rev_p = sub.add_parser("review", help="Review task operations")
    rev_sub = rev_p.add_subparsers(dest="review_cmd")

    p = rev_sub.add_parser("request", help="Request review for an intent")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--reviewer")
    p.add_argument("--trigger", default="manual", choices=["policy", "conflict", "manual"])
    p.add_argument("--priority", type=int)
    p.add_argument("--tenant-id")

    p = rev_sub.add_parser("list", help="List review tasks")
    p.add_argument("--intent-id")
    p.add_argument("--status", choices=["pending", "assigned", "in_review", "escalated", "completed", "cancelled"])
    p.add_argument("--reviewer")
    p.add_argument("--tenant-id")
    p.add_argument("--limit", type=int, default=50)

    p = rev_sub.add_parser("assign", help="Assign review to reviewer")
    p.add_argument("--task-id", required=True)
    p.add_argument("--reviewer", required=True)

    p = rev_sub.add_parser("complete", help="Complete a review")
    p.add_argument("--task-id", required=True)
    p.add_argument("--resolution", required=True, choices=["approved", "rejected", "deferred"])
    p.add_argument("--notes", default="")

    p = rev_sub.add_parser("cancel", help="Cancel a review")
    p.add_argument("--task-id", required=True)
    p.add_argument("--reason", default="")

    p = rev_sub.add_parser("escalate", help="Escalate a review")
    p.add_argument("--task-id", required=True)
    p.add_argument("--reason", default="manual_escalation")

    p = rev_sub.add_parser("sla-check", help="Check for SLA breaches")
    p.add_argument("--tenant-id")

    p = rev_sub.add_parser("summary", help="Review summary for dashboard")
    p.add_argument("--tenant-id")


def _register_intake_commands(sub: argparse._SubParsersAction) -> None:
    # -- intake --
    intake_p = sub.add_parser("intake", help="Intake control")
    intake_sub = intake_p.add_subparsers(dest="intake_cmd")

    p = intake_sub.add_parser("status", help="Current intake mode and health signals")
    p.add_argument("--tenant-id")

    p = intake_sub.add_parser("set-mode", help="Manually override intake mode")
    p.add_argument("mode", choices=["open", "throttle", "pause", "auto"])
    p.add_argument("--tenant-id")
    p.add_argument("--reason", default="")

    # -- security --
    sec_p = sub.add_parser("security", help="Security scanning")
    sec_sub = sec_p.add_subparsers(dest="security_cmd")

    p = sec_sub.add_parser("scan", help="Run security scan")
    p.add_argument("--path", default=".", help="Path to scan")
    p.add_argument("--intent-id")
    p.add_argument("--tenant-id")

    p = sec_sub.add_parser("findings", help="List security findings")
    p.add_argument("--intent-id")
    p.add_argument("--scanner")
    p.add_argument("--severity", choices=["critical", "high", "medium", "low", "info"])
    p.add_argument("--category", choices=["sast", "sca", "secrets"])
    p.add_argument("--tenant-id")
    p.add_argument("--limit", type=int, default=100)

    p = sec_sub.add_parser("summary", help="Security findings summary")
    p.add_argument("--tenant-id")


def _register_export_commands(sub: argparse._SubParsersAction) -> None:
    # -- export --
    export_p = sub.add_parser("export", help="Export data")
    export_sub = export_p.add_subparsers(dest="export_cmd")

    p = export_sub.add_parser("decisions", help="Export decision dataset (JSONL/CSV)")
    p.add_argument("--output")
    p.add_argument("--tenant-id")
    p.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")

    # -- harness --
    p = sub.add_parser("harness", help="Pre-evaluation harness")
    harness_sub = p.add_subparsers(dest="harness_cmd")

    p = harness_sub.add_parser("evaluate", help="Pre-evaluate a draft intent")
    p.add_argument("--file", required=True, help="JSON file with draft intent data")
    p.add_argument("--mode", choices=["shadow", "enforce"], default="shadow")
    p.add_argument("--tenant-id")


def _register_coherence_commands(sub: argparse._SubParsersAction) -> None:
    # -- coherence --
    coh_p = sub.add_parser("coherence", help="Coherence harness operations")
    coh_sub = coh_p.add_subparsers(dest="coherence_cmd")

    coh_sub.add_parser("init", help="Create coherence harness config with template")

    p = coh_sub.add_parser("list", help="List configured questions and baselines")
    p.add_argument("--path", help="Path to harness config file")

    p = coh_sub.add_parser("run", help="Run coherence harness against current state")
    p.add_argument("--path", help="Path to harness config file")

    coh_sub.add_parser("baseline", help="Update baselines from current state")

    p = coh_sub.add_parser("suggest", help="Analyze failures and suggest new questions")
    p.add_argument("--lookback-days", type=int, default=90)

    p = coh_sub.add_parser("accept", help="Accept a feedback suggestion")
    p.add_argument("--suggestion-id", required=True)


def _register_server_commands(sub: argparse._SubParsersAction) -> None:
    # -- serve --
    p = sub.add_parser("serve", help="Start HTTP API server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9876)
    p.add_argument("--secret")
    p.add_argument("--ui-dist", default="", help="Path to built UI dist directory for single-process deployment")

    # -- worker --
    sub.add_parser("worker", help="Start queue worker process")
