"""Projection output types: HealthSnapshot, ComplianceReport, QueueState.

Extracted from models.py to keep domain model module under LOC limit.
These are read-only output shapes used by the projections subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from converge.models import now_iso


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
class DebtSnapshot:
    debt_score: float = 0.0
    staleness_score: float = 0.0
    queue_pressure_score: float = 0.0
    review_backlog_score: float = 0.0
    conflict_pressure_score: float = 0.0
    retry_pressure_score: float = 0.0
    breakdown: dict[str, Any] = field(default_factory=dict)
    status: str = "green"  # green / yellow / red
    timestamp: str = field(default_factory=now_iso)
    tenant_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "debt_score": self.debt_score,
            "staleness_score": self.staleness_score,
            "queue_pressure_score": self.queue_pressure_score,
            "review_backlog_score": self.review_backlog_score,
            "conflict_pressure_score": self.conflict_pressure_score,
            "retry_pressure_score": self.retry_pressure_score,
            "breakdown": self.breakdown,
            "status": self.status,
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
