"""Argparse parser definition for Converge CLI."""

from __future__ import annotations

import argparse

from converge.cli._helpers import _default_db
from converge.models import Status


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

    # -- worker --
    sub.add_parser("worker", help="Start queue worker process")

    return parser
