"""Typed event payloads for the core engine events.

These dataclasses enforce schema consistency for event payloads that are:
- Constructed in engine.py (the hot path)
- Queried later by projections, analytics, and the API layer

Simple payloads (e.g. {"task_id": "x", "reviewer": "y"}) don't need types —
the cost of a dataclass exceeds the risk of a typo in 2-field dicts.

Invariant: Every field in these payloads is named exactly once.  A typo
in a field name causes a Python error at construction time, not a silent
missing-key bug at query time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SimulationPayload:
    """Payload for EventType.SIMULATION_COMPLETED."""
    mergeable: bool
    conflicts: list[str]
    files_changed: list[str]
    source: str
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mergeable": self.mergeable,
            "conflicts": self.conflicts,
            "files_changed": self.files_changed,
            "source": self.source,
            "target": self.target,
        }


@dataclass(frozen=True)
class CheckPayload:
    """Payload for EventType.CHECK_COMPLETED."""
    check_type: str
    passed: bool
    details: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_type": self.check_type,
            "passed": self.passed,
            "details": self.details,
        }


@dataclass(frozen=True)
class GatePayload:
    """Single gate result within a PolicyPayload."""
    gate: str
    passed: bool
    reason: str
    value: float = 0.0
    threshold: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "passed": self.passed,
            "reason": self.reason,
            "value": self.value,
            "threshold": self.threshold,
        }


@dataclass(frozen=True)
class PolicyPayload:
    """Payload for EventType.POLICY_EVALUATED."""
    verdict: str
    gates: list[GatePayload]
    profile_used: str
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "gates": [g.to_dict() for g in self.gates],
            "profile_used": self.profile_used,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class BlockPayload:
    """Payload for EventType.INTENT_BLOCKED."""
    reason: str
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "trace_id": self.trace_id}


@dataclass(frozen=True)
class MergePayload:
    """Payload for EventType.INTENT_MERGED."""
    merged_commit: str
    source: str
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "merged_commit": self.merged_commit,
            "source": self.source,
            "target": self.target,
        }


@dataclass(frozen=True)
class MergeFailedPayload:
    """Payload for EventType.INTENT_MERGE_FAILED."""
    error: str
    source: str
    target: str
    retries: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error,
            "source": self.source,
            "target": self.target,
            "retries": self.retries,
        }


@dataclass(frozen=True)
class RejectPayload:
    """Payload for EventType.INTENT_REJECTED / INTENT_REQUEUED."""
    reason: str
    retries: int

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "retries": self.retries}


@dataclass(frozen=True)
class IntakePayload:
    """Payload for intake events (accepted/throttled/rejected)."""
    mode: str
    accepted: bool
    risk_level: str
    origin_type: str
    signals: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "mode": self.mode,
            "accepted": self.accepted,
            "risk_level": self.risk_level,
            "origin_type": self.origin_type,
            "signals": self.signals,
        }
        if self.reason:
            d["reason"] = self.reason
        return d
