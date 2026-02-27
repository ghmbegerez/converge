#!/usr/bin/env python3
"""Seed a Converge database with realistic demo data.

Usage:
    PYTHONPATH=src python3 scripts/seed_demo_data.py [--db-path .converge/state.db]

Inserts intents, lifecycle events, security findings, review tasks,
and policy configuration using the event_log facade directly.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from converge import event_log as el
from converge.models import (
    Event,
    Intent,
    ReviewTask,
    ReviewStatus,
    RiskLevel,
    Status,
    now_iso,
)
from converge.event_types import EventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(days_ago: float, hours: float = 0) -> str:
    """Return an ISO timestamp *days_ago* days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours)
    return dt.isoformat()


TENANT = "demo"


# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------

INTENTS: list[dict] = [
    dict(id="intent-001", source="feature/auth-refactor",   target="main", status=Status.MERGED,    risk=RiskLevel.HIGH,     prio=1, origin="human",       days=7),
    dict(id="intent-002", source="feature/payment-gateway", target="main", status=Status.MERGED,    risk=RiskLevel.CRITICAL, prio=1, origin="human",       days=6),
    dict(id="intent-003", source="feature/dark-mode",       target="main", status=Status.MERGED,    risk=RiskLevel.LOW,      prio=3, origin="agent",       days=5),
    dict(id="intent-004", source="fix/null-pointer",        target="main", status=Status.MERGED,    risk=RiskLevel.MEDIUM,   prio=2, origin="human",       days=4),
    dict(id="intent-005", source="feature/search-v2",       target="main", status=Status.REJECTED,  risk=RiskLevel.HIGH,     prio=2, origin="agent",       days=4),
    dict(id="intent-006", source="chore/dep-update",        target="main", status=Status.REJECTED,  risk=RiskLevel.MEDIUM,   prio=4, origin="integration", days=3),
    dict(id="intent-007", source="feature/notifications",   target="main", status=Status.QUEUED,    risk=RiskLevel.MEDIUM,   prio=2, origin="human",       days=2),
    dict(id="intent-008", source="feature/caching-layer",   target="main", status=Status.QUEUED,    risk=RiskLevel.HIGH,     prio=1, origin="agent",       days=2),
    dict(id="intent-009", source="feature/admin-panel",     target="main", status=Status.VALIDATED, risk=RiskLevel.MEDIUM,   prio=3, origin="human",       days=1),
    dict(id="intent-010", source="fix/race-condition",      target="main", status=Status.VALIDATED, risk=RiskLevel.HIGH,     prio=1, origin="human",       days=1),
    dict(id="intent-011", source="feature/api-v3",          target="main", status=Status.READY,     risk=RiskLevel.LOW,      prio=3, origin="agent",       days=0.5),
    dict(id="intent-012", source="feature/logging",         target="main", status=Status.READY,     risk=RiskLevel.LOW,      prio=4, origin="integration", days=0.2),
]


def _seed_intents() -> None:
    for spec in INTENTS:
        intent = Intent(
            id=spec["id"],
            source=spec["source"],
            target=spec["target"],
            status=spec["status"],
            created_at=_iso(spec["days"]),
            risk_level=spec["risk"],
            priority=spec["prio"],
            origin_type=spec["origin"],
            tenant_id=TENANT,
            semantic={"description": f"Demo intent for {spec['source']}"},
            technical={"files_changed": ["src/app.py", "tests/test_app.py"]},
        )
        el.upsert_intent(intent)


# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------

_LIFECYCLE: list[tuple[str, str, str, float]] = [
    # (intent_id, event_type, status_payload_key, days_ago)
    ("intent-001", EventType.INTENT_CREATED,        "READY",     7.0),
    ("intent-001", EventType.SIMULATION_COMPLETED,   "READY",     6.8),
    ("intent-001", EventType.RISK_EVALUATED,         "READY",     6.7),
    ("intent-001", EventType.POLICY_EVALUATED,       "VALIDATED", 6.5),
    ("intent-001", EventType.INTENT_MERGED,          "MERGED",    6.0),

    ("intent-002", EventType.INTENT_CREATED,        "READY",     6.0),
    ("intent-002", EventType.SIMULATION_COMPLETED,   "READY",     5.8),
    ("intent-002", EventType.RISK_EVALUATED,         "READY",     5.7),
    ("intent-002", EventType.POLICY_EVALUATED,       "VALIDATED", 5.5),
    ("intent-002", EventType.INTENT_MERGED,          "MERGED",    5.0),

    ("intent-003", EventType.INTENT_CREATED,        "READY",     5.0),
    ("intent-003", EventType.SIMULATION_COMPLETED,   "READY",     4.9),
    ("intent-003", EventType.INTENT_MERGED,          "MERGED",    4.5),

    ("intent-004", EventType.INTENT_CREATED,        "READY",     4.0),
    ("intent-004", EventType.RISK_EVALUATED,         "READY",     3.8),
    ("intent-004", EventType.INTENT_MERGED,          "MERGED",    3.5),

    ("intent-005", EventType.INTENT_CREATED,        "READY",     4.0),
    ("intent-005", EventType.POLICY_EVALUATED,       "READY",     3.9),
    ("intent-005", EventType.INTENT_REJECTED,        "REJECTED",  3.8),

    ("intent-006", EventType.INTENT_CREATED,        "READY",     3.0),
    ("intent-006", EventType.INTENT_REJECTED,        "REJECTED",  2.8),

    ("intent-007", EventType.INTENT_CREATED,        "READY",     2.0),
    ("intent-007", EventType.SIMULATION_COMPLETED,   "READY",     1.9),
    ("intent-007", EventType.RISK_EVALUATED,         "READY",     1.8),
    ("intent-007", EventType.POLICY_EVALUATED,       "VALIDATED", 1.7),

    ("intent-008", EventType.INTENT_CREATED,        "READY",     2.0),
    ("intent-008", EventType.RISK_EVALUATED,         "READY",     1.9),
    ("intent-008", EventType.POLICY_EVALUATED,       "VALIDATED", 1.8),

    ("intent-009", EventType.INTENT_CREATED,        "READY",     1.0),
    ("intent-009", EventType.SIMULATION_COMPLETED,   "READY",     0.9),

    ("intent-010", EventType.INTENT_CREATED,        "READY",     1.0),
    ("intent-010", EventType.RISK_EVALUATED,         "READY",     0.8),

    ("intent-011", EventType.INTENT_CREATED,        "READY",     0.5),
    ("intent-012", EventType.INTENT_CREATED,        "READY",     0.2),
]


def _seed_events() -> None:
    for intent_id, etype, status_val, days_ago in _LIFECYCLE:
        el.append(Event(
            event_type=etype,
            intent_id=intent_id,
            tenant_id=TENANT,
            timestamp=_iso(days_ago),
            payload={"status": status_val, "source": "seed_demo_data"},
        ))


# ---------------------------------------------------------------------------
# Security findings
# ---------------------------------------------------------------------------

_FINDINGS: list[dict] = [
    dict(id="finding-001", scanner="bandit",    category="sast",    severity="critical", file="src/auth.py",     line=42, rule="B105", evidence="Hardcoded password string"),
    dict(id="finding-002", scanner="bandit",    category="sast",    severity="high",     file="src/crypto.py",   line=18, rule="B303", evidence="Use of insecure MD5 hash"),
    dict(id="finding-003", scanner="bandit",    category="sast",    severity="high",     file="src/api.py",      line=77, rule="B201", evidence="Flask debug mode enabled"),
    dict(id="finding-004", scanner="pip-audit", category="sca",     severity="medium",   file="requirements.txt",line=5,  rule="PYSEC-2024-001", evidence="Vulnerable dependency: requests<2.32"),
    dict(id="finding-005", scanner="pip-audit", category="sca",     severity="medium",   file="requirements.txt",line=12, rule="PYSEC-2024-015", evidence="Vulnerable dependency: cryptography<42"),
    dict(id="finding-006", scanner="pip-audit", category="sca",     severity="medium",   file="requirements.txt",line=8,  rule="PYSEC-2024-022", evidence="Vulnerable dependency: urllib3<2.2"),
    dict(id="finding-007", scanner="gitleaks",  category="secrets",  severity="low",      file=".env.example",   line=3,  rule="generic-api-key", evidence="Possible API key in example file"),
    dict(id="finding-008", scanner="gitleaks",  category="secrets",  severity="low",      file="docs/setup.md",  line=15, rule="generic-api-key", evidence="Placeholder API key in docs"),
]


def _seed_security_findings() -> None:
    for f in _FINDINGS:
        el.upsert_security_finding({
            "id": f["id"],
            "scanner": f["scanner"],
            "category": f["category"],
            "severity": f["severity"],
            "file": f["file"],
            "line": f["line"],
            "rule": f["rule"],
            "evidence": f["evidence"],
            "confidence": "high",
            "intent_id": "intent-002" if f["severity"] == "critical" else None,
            "tenant_id": TENANT,
            "timestamp": _iso(3),
        })


# ---------------------------------------------------------------------------
# Review tasks
# ---------------------------------------------------------------------------

_REVIEWS: list[dict] = [
    dict(id="review-001", intent="intent-002", status=ReviewStatus.COMPLETED, reviewer="alice",  risk=RiskLevel.CRITICAL, trigger="policy",   resolution="approved"),
    dict(id="review-002", intent="intent-005", status=ReviewStatus.COMPLETED, reviewer="bob",    risk=RiskLevel.HIGH,     trigger="conflict", resolution="rejected"),
    dict(id="review-003", intent="intent-008", status=ReviewStatus.IN_REVIEW, reviewer="carol",  risk=RiskLevel.HIGH,     trigger="policy",   resolution=None),
    dict(id="review-004", intent="intent-010", status=ReviewStatus.ASSIGNED,  reviewer="dave",   risk=RiskLevel.HIGH,     trigger="policy",   resolution=None),
    dict(id="review-005", intent="intent-009", status=ReviewStatus.PENDING,   reviewer=None,     risk=RiskLevel.MEDIUM,   trigger="manual",   resolution=None),
]


def _seed_review_tasks() -> None:
    for r in _REVIEWS:
        task = ReviewTask(
            id=r["id"],
            intent_id=r["intent"],
            status=r["status"],
            reviewer=r["reviewer"],
            risk_level=r["risk"],
            trigger=r["trigger"],
            resolution=r["resolution"],
            tenant_id=TENANT,
            created_at=_iso(3),
            assigned_at=_iso(2.5) if r["reviewer"] else None,
            completed_at=_iso(2) if r["status"] == ReviewStatus.COMPLETED else None,
        )
        el.upsert_review_task(task)


# ---------------------------------------------------------------------------
# Policies & thresholds
# ---------------------------------------------------------------------------

def _seed_policies() -> None:
    el.upsert_compliance_thresholds(TENANT, {
        "min_test_coverage": 0.70,
        "max_open_critical_findings": 0,
        "max_open_high_findings": 3,
        "require_review_on_critical": True,
        "max_cycle_time_hours": 48,
    })

    el.upsert_risk_policy(TENANT, {
        "entropy_budget": 15.0,
        "containment_threshold": 0.6,
        "profiles": {
            "low":      {"required_checks": ["lint"]},
            "medium":   {"required_checks": ["lint", "unit_tests"]},
            "high":     {"required_checks": ["lint", "unit_tests", "integration_tests"]},
            "critical": {"required_checks": ["lint", "unit_tests", "integration_tests", "security_scan"]},
        },
    })

    el.upsert_agent_policy({
        "agent_id": "claude",
        "tenant_id": TENANT,
        "atl": 2,
        "max_risk_score": 50.0,
        "max_blast_severity": "medium",
        "allow_actions": ["analyze", "implement", "test"],
        "require_human_approval": True,
    })

    el.upsert_agent_policy({
        "agent_id": "codex",
        "tenant_id": TENANT,
        "atl": 1,
        "max_risk_score": 25.0,
        "max_blast_severity": "low",
        "allow_actions": ["analyze"],
        "require_human_approval": True,
    })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Converge demo data")
    parser.add_argument(
        "--db-path",
        default=".converge/state.db",
        help="Path to SQLite database (default: .converge/state.db)",
    )
    args = parser.parse_args()

    el.init(args.db_path)

    print(f"Seeding database: {args.db_path}")
    _seed_intents()
    print("  12 intents inserted")
    _seed_events()
    print("  34 lifecycle events inserted")
    _seed_security_findings()
    print("  8 security findings inserted")
    _seed_review_tasks()
    print("  5 review tasks inserted")
    _seed_policies()
    print("  4 policies/thresholds inserted")

    el.close()
    print("Done.")


if __name__ == "__main__":
    main()
