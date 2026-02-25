"""Core data types for Converge."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Status(str, Enum):
    READY = "READY"
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    MERGED = "MERGED"
    REJECTED = "REJECTED"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_REVIEW = "in_review"
    ESCALATED = "escalated"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PolicyVerdict(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class GateName(str, Enum):
    VERIFICATION = "verification"
    CONTAINMENT = "containment"
    ENTROPY = "entropy"
    RISK = "risk"
    SECURITY = "security"
    COHERENCE = "coherence"


class CoherenceVerdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


from converge.event_types import EventType  # noqa: F401
from converge.security_models import (  # noqa: F401
    FindingCategory,
    FindingSeverity,
    SecurityFinding,
)


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

@dataclass
class Intent:
    id: str
    source: str
    target: str
    status: Status
    created_at: str = field(default_factory=now_iso)
    created_by: str = "system"
    risk_level: RiskLevel = RiskLevel.MEDIUM
    priority: int = 3
    semantic: dict[str, Any] = field(default_factory=dict)
    technical: dict[str, Any] = field(default_factory=dict)
    checks_required: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    retries: int = 0
    tenant_id: str | None = None
    plan_id: str | None = None
    origin_type: str = "human"  # human | agent | integration

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "status": self.status.value,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "risk_level": self.risk_level.value,
            "priority": self.priority,
            "semantic": self.semantic,
            "technical": self.technical,
            "checks_required": self.checks_required,
            "dependencies": self.dependencies,
            "retries": self.retries,
            "tenant_id": self.tenant_id,
            "plan_id": self.plan_id,
            "origin_type": self.origin_type,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Intent:
        return cls(
            id=d["id"],
            source=d.get("source", d.get("technical", {}).get("source_ref", "")),
            target=d.get("target", d.get("technical", {}).get("target_ref", "main")),
            status=Status(d.get("status", "READY")),
            created_at=d.get("created_at", now_iso()),
            created_by=d.get("created_by", "system"),
            risk_level=RiskLevel(d.get("risk_level", "medium")),
            priority=d.get("priority", 3),
            semantic=d.get("semantic", {}),
            technical=d.get("technical", {}),
            checks_required=d.get("checks_required", []),
            dependencies=d.get("dependencies", []),
            retries=d.get("retries", 0),
            tenant_id=d.get("tenant_id"),
            plan_id=d.get("plan_id"),
            origin_type=d.get("origin_type", "human"),
        )


# ---------------------------------------------------------------------------
# Review task
# ---------------------------------------------------------------------------

@dataclass
class ReviewTask:
    id: str
    intent_id: str
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    priority: int = 3
    risk_level: RiskLevel = RiskLevel.MEDIUM
    trigger: str = "policy"  # policy | conflict | manual
    sla_deadline: str | None = None
    created_at: str = field(default_factory=now_iso)
    assigned_at: str | None = None
    completed_at: str | None = None
    escalated_at: str | None = None
    resolution: str | None = None  # approved | rejected | deferred
    notes: str = ""
    tenant_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "intent_id": self.intent_id,
            "status": self.status.value,
            "reviewer": self.reviewer,
            "priority": self.priority,
            "risk_level": self.risk_level.value,
            "trigger": self.trigger,
            "sla_deadline": self.sla_deadline,
            "created_at": self.created_at,
            "assigned_at": self.assigned_at,
            "completed_at": self.completed_at,
            "escalated_at": self.escalated_at,
            "resolution": self.resolution,
            "notes": self.notes,
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReviewTask:
        return cls(
            id=d["id"],
            intent_id=d["intent_id"],
            status=ReviewStatus(d.get("status", "pending")),
            reviewer=d.get("reviewer"),
            priority=d.get("priority", 3),
            risk_level=RiskLevel(d.get("risk_level", "medium")),
            trigger=d.get("trigger", "policy"),
            sla_deadline=d.get("sla_deadline"),
            created_at=d.get("created_at", now_iso()),
            assigned_at=d.get("assigned_at"),
            completed_at=d.get("completed_at"),
            escalated_at=d.get("escalated_at"),
            resolution=d.get("resolution"),
            notes=d.get("notes", ""),
            tenant_id=d.get("tenant_id"),
        )


# ---------------------------------------------------------------------------
# Commit links
# ---------------------------------------------------------------------------

@dataclass
class CommitLink:
    intent_id: str
    repo: str
    sha: str
    role: str = "head"  # head | base | merge
    observed_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "repo": self.repo,
            "sha": self.sha,
            "role": self.role,
            "observed_at": self.observed_at,
        }


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

@dataclass
class Simulation:
    mergeable: bool
    conflicts: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=now_iso)
    source: str = ""
    target: str = ""


# ---------------------------------------------------------------------------
# Check result
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    check_type: str
    passed: bool
    details: str = ""
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    gate: GateName
    passed: bool
    reason: str
    value: float = 0.0
    threshold: float = 0.0


@dataclass
class PolicyEvaluation:
    verdict: PolicyVerdict
    gates: list[GateResult] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    profile_used: str = "medium"


# ---------------------------------------------------------------------------
# Risk evaluation
# ---------------------------------------------------------------------------

@dataclass
class RiskEval:
    intent_id: str
    risk_score: float = 0.0
    damage_score: float = 0.0
    entropy_score: float = 0.0
    propagation_score: float = 0.0
    containment_score: float = 0.0
    # 4 independent signals
    entropic_load: float = 0.0       # disorder introduced by the change
    contextual_value: float = 0.0    # importance of touched files (PageRank)
    complexity_delta: float = 0.0    # net complexity change to the system
    path_dependence: float = 0.0     # sensitivity to merge order
    findings: list[dict[str, Any]] = field(default_factory=list)
    impact_edges: list[dict[str, Any]] = field(default_factory=list)
    graph_metrics: dict[str, Any] = field(default_factory=dict)
    bombs: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=now_iso)
    tenant_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "risk_score": self.risk_score,
            "damage_score": self.damage_score,
            "entropy_score": self.entropy_score,
            "propagation_score": self.propagation_score,
            "containment_score": self.containment_score,
            "signals": {
                "entropic_load": self.entropic_load,
                "contextual_value": self.contextual_value,
                "complexity_delta": self.complexity_delta,
                "path_dependence": self.path_dependence,
            },
            "findings": self.findings,
            "impact_edges": self.impact_edges,
            "graph_metrics": self.graph_metrics,
            "bombs": self.bombs,
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
        }


# ---------------------------------------------------------------------------
# Coherence harness
# ---------------------------------------------------------------------------

@dataclass
class CoherenceQuestion:
    id: str
    question: str
    check: str
    assertion: str
    severity: str = "high"       # critical | high | medium
    category: str = "structural"  # structural | semantic | health


@dataclass
class CoherenceResult:
    question_id: str
    question: str
    verdict: str    # pass | warn | fail
    value: float
    baseline: float | None
    assertion: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "verdict": self.verdict,
            "value": self.value,
            "baseline": self.baseline,
            "assertion": self.assertion,
            "error": self.error,
        }


@dataclass
class CoherenceEvaluation:
    coherence_score: float          # 0-100
    verdict: str                    # pass | warn | fail
    results: list[CoherenceResult]
    harness_version: str
    inconsistencies: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "coherence_score": self.coherence_score,
            "verdict": self.verdict,
            "results": [r.to_dict() for r in self.results],
            "harness_version": self.harness_version,
            "inconsistencies": self.inconsistencies,
        }


# ---------------------------------------------------------------------------
# Event (the universal record)
# ---------------------------------------------------------------------------

@dataclass
class Event:
    event_type: str
    payload: dict[str, Any]
    id: str = field(default_factory=new_id)
    trace_id: str = ""
    timestamp: str = field(default_factory=now_iso)
    intent_id: str | None = None
    agent_id: str | None = None
    tenant_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "intent_id": self.intent_id,
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "payload": self.payload,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Agent policy
# ---------------------------------------------------------------------------

@dataclass
class AgentPolicy:
    agent_id: str
    tenant_id: str | None = None
    atl: int = 0  # autonomy trust level 0-3
    max_risk_score: float = 30.0
    max_blast_severity: str = "low"
    min_test_coverage: float = 0.0
    require_compliance_pass: bool = True
    require_human_approval: bool = True
    require_dual_approval_on_critical: bool = True
    allow_actions: list[str] = field(default_factory=lambda: ["analyze"])
    action_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "atl": self.atl,
            "max_risk_score": self.max_risk_score,
            "max_blast_severity": self.max_blast_severity,
            "min_test_coverage": self.min_test_coverage,
            "require_compliance_pass": self.require_compliance_pass,
            "require_human_approval": self.require_human_approval,
            "require_dual_approval_on_critical": self.require_dual_approval_on_critical,
            "allow_actions": self.allow_actions,
            "action_overrides": self.action_overrides,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentPolicy:
        return cls(
            agent_id=d["agent_id"],
            tenant_id=d.get("tenant_id"),
            atl=d.get("atl", 0),
            max_risk_score=d.get("max_risk_score", 30.0),
            max_blast_severity=d.get("max_blast_severity", "low"),
            min_test_coverage=d.get("min_test_coverage", 0.0),
            require_compliance_pass=d.get("require_compliance_pass", True),
            require_human_approval=d.get("require_human_approval", True),
            require_dual_approval_on_critical=d.get("require_dual_approval_on_critical", True),
            allow_actions=d.get("allow_actions", ["analyze"]),
            action_overrides=d.get("action_overrides", {}),
            expires_at=d.get("expires_at"),
        )

