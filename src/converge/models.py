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


class PolicyVerdict(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class GateName(str, Enum):
    VERIFICATION = "verification"
    CONTAINMENT = "containment"
    ENTROPY = "entropy"
    RISK = "risk"


# ---------------------------------------------------------------------------
# Event type registry (single source of truth for all event type strings)
# ---------------------------------------------------------------------------

class EventType:
    # Simulation
    SIMULATION_COMPLETED = "simulation.completed"
    # Checks
    CHECK_COMPLETED = "check.completed"
    # Risk
    RISK_EVALUATED = "risk.evaluated"
    RISK_SHADOW_EVALUATED = "risk.shadow_evaluated"
    RISK_POLICY_UPDATED = "risk.policy_updated"
    # Policy
    POLICY_EVALUATED = "policy.evaluated"
    # Intent lifecycle
    INTENT_CREATED = "intent.created"
    INTENT_STATUS_CHANGED = "intent.status_changed"
    INTENT_VALIDATED = "intent.validated"
    INTENT_BLOCKED = "intent.blocked"
    INTENT_REJECTED = "intent.rejected"
    INTENT_REQUEUED = "intent.requeued"
    INTENT_MERGED = "intent.merged"
    # Queue
    QUEUE_PROCESSED = "queue.processed"
    QUEUE_RESET = "queue.reset"
    # Health
    HEALTH_SNAPSHOT = "health.snapshot"
    HEALTH_CHANGE_SNAPSHOT = "health.change_snapshot"
    HEALTH_PREDICTION = "health.prediction"
    # Compliance
    COMPLIANCE_THRESHOLDS_UPDATED = "compliance.thresholds_updated"
    # Agent
    AGENT_POLICY_UPDATED = "agent.policy_updated"
    AGENT_AUTHORIZED = "agent.authorized"
    # Analytics
    CALIBRATION_COMPLETED = "calibration.completed"
    DATASET_EXPORTED = "dataset.exported"
    # Integrations
    WEBHOOK_RECEIVED = "webhook.received"
    # GitHub
    GITHUB_DECISION_PUBLISHED = "github.decision_published"
    GITHUB_DECISION_PUBLISH_FAILED = "github.decision_publish_failed"
    # Merge Queue
    MERGE_GROUP_CHECKS_REQUESTED = "merge_group.checks_requested"
    MERGE_GROUP_DESTROYED = "merge_group.destroyed"
    # Worker
    WORKER_STARTED = "worker.started"
    WORKER_STOPPED = "worker.stopped"


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
        )


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


# ---------------------------------------------------------------------------
# Projections (output types)
# ---------------------------------------------------------------------------

@dataclass
class HealthSnapshot:
    repo_health_score: float = 100.0
    entropy_score: float = 0.0
    mergeable_rate: float = 1.0
    conflict_rate: float = 0.0
    active_intents: int = 0
    merged_last_24h: int = 0
    rejected_last_24h: int = 0
    status: str = "green"  # green / yellow / red
    timestamp: str = field(default_factory=now_iso)
    tenant_id: str | None = None
    learning: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_health_score": self.repo_health_score,
            "entropy_score": self.entropy_score,
            "mergeable_rate": self.mergeable_rate,
            "conflict_rate": self.conflict_rate,
            "active_intents": self.active_intents,
            "merged_last_24h": self.merged_last_24h,
            "rejected_last_24h": self.rejected_last_24h,
            "status": self.status,
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
            "learning": self.learning,
        }


@dataclass
class ComplianceReport:
    mergeable_rate: float = 1.0
    conflict_rate: float = 0.0
    retries_total: int = 0
    queue_tracked: int = 0
    checks: list[dict[str, Any]] = field(default_factory=list)
    passed: bool = True
    alerts: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=now_iso)
    tenant_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mergeable_rate": self.mergeable_rate,
            "conflict_rate": self.conflict_rate,
            "retries_total": self.retries_total,
            "queue_tracked": self.queue_tracked,
            "checks": self.checks,
            "passed": self.passed,
            "alerts": self.alerts,
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
        }


@dataclass
class QueueState:
    pending: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"pending": self.pending, "total": self.total, "by_status": self.by_status}
