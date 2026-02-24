"""Adaptive intake control: evaluate system health before accepting intents.

Three modes:
  - open: accept all intents (normal operation)
  - throttle: probabilistic rate limiting (accept ~throttle_ratio of intents)
  - pause: only critical-risk intents accepted

Mode is auto-computed from health signals or manually overridden via API/CLI.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from converge import event_log, projections
from converge.defaults import (
    INTAKE_PAUSE_BELOW_HEALTH,
    INTAKE_THROTTLE_BELOW_HEALTH,
    INTAKE_THROTTLE_RATIO,
    ROLLOUT_DIVISOR,
    ROLLOUT_HASH_CHARS,
)
from converge.event_payloads import IntakePayload
from converge.models import Event, Intent, RiskLevel, now_iso

from converge.event_types import EventType


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class IntakeMode(str, Enum):
    OPEN = "open"
    THROTTLE = "throttle"
    PAUSE = "pause"


@dataclass
class IntakeDecision:
    accepted: bool
    mode: IntakeMode
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Default thresholds (overridable via policy config intake section)
# ---------------------------------------------------------------------------

DEFAULT_INTAKE_CONFIG: dict[str, Any] = {
    "pause_below_health": INTAKE_PAUSE_BELOW_HEALTH,
    "throttle_below_health": INTAKE_THROTTLE_BELOW_HEALTH,
    "throttle_ratio": INTAKE_THROTTLE_RATIO,
}


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_intake(
    intent: Intent,
    *,
    config: dict[str, Any] | None = None,
) -> IntakeDecision:
    """Evaluate whether to accept an intent based on system health.

    Returns an IntakeDecision. If not accepted, the caller should NOT
    persist the intent — only the intake event is emitted.
    """
    cfg = config or _load_intake_config()
    tenant_id = intent.tenant_id

    # Resolve mode: manual override takes precedence over auto-computed
    mode, signals = _resolve_mode(tenant_id=tenant_id, config=cfg)

    # Apply mode rules
    if mode == IntakeMode.OPEN:
        decision = IntakeDecision(
            accepted=True, mode=mode,
            reason="open mode: accepting all intents",
            signals=signals,
        )
    elif mode == IntakeMode.PAUSE:
        if intent.risk_level == RiskLevel.CRITICAL:
            decision = IntakeDecision(
                accepted=True, mode=mode,
                reason="pause mode: critical-risk intent accepted",
                signals=signals,
            )
        else:
            decision = IntakeDecision(
                accepted=False, mode=mode,
                reason=f"pause mode: only critical-risk intents accepted (got {intent.risk_level.value})",
                signals=signals,
            )
    else:
        # Throttle: deterministic bucket by intent ID
        bucket = _throttle_bucket(intent.id)
        ratio = cfg.get("throttle_ratio", DEFAULT_INTAKE_CONFIG["throttle_ratio"])
        if bucket < ratio:
            decision = IntakeDecision(
                accepted=True, mode=mode,
                reason=f"throttle mode: accepted (bucket={bucket:.4f} < ratio={ratio})",
                signals={**signals, "bucket": round(bucket, 4), "throttle_ratio": ratio},
            )
        else:
            decision = IntakeDecision(
                accepted=False, mode=mode,
                reason=f"throttle mode: rejected (bucket={bucket:.4f} >= ratio={ratio})",
                signals={**signals, "bucket": round(bucket, 4), "throttle_ratio": ratio},
            )

    # Emit intake event
    _emit_intake_event(intent, decision)

    return decision


def intake_status(
    *,
    tenant_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Current intake status for dashboard / CLI."""
    cfg = config or _load_intake_config()
    mode, signals = _resolve_mode(tenant_id=tenant_id, config=cfg)

    # Check for manual override
    override = event_log.get_intake_override(tenant_id=tenant_id or "")
    has_override = override is not None

    return {
        "mode": mode.value,
        "auto_mode": signals.get("auto_mode", mode.value),
        "manual_override": has_override,
        "override": override if has_override else None,
        "signals": signals,
        "config": {
            "pause_below_health": cfg.get("pause_below_health", DEFAULT_INTAKE_CONFIG["pause_below_health"]),
            "throttle_below_health": cfg.get("throttle_below_health", DEFAULT_INTAKE_CONFIG["throttle_below_health"]),
            "throttle_ratio": cfg.get("throttle_ratio", DEFAULT_INTAKE_CONFIG["throttle_ratio"]),
        },
        "tenant_id": tenant_id,
    }


def set_intake_mode(
    mode: str,
    *,
    tenant_id: str | None = None,
    set_by: str = "operator",
    reason: str = "",
) -> dict[str, Any]:
    """Manually override intake mode for a tenant.

    Pass mode="auto" to clear the override and revert to auto-computed mode.
    """
    tenant = tenant_id or ""

    if mode == "auto":
        event_log.delete_intake_override(tenant_id=tenant)
        event_log.append(Event(
            event_type=EventType.INTAKE_MODE_CHANGED,
            tenant_id=tenant_id,
            payload={
                "mode": "auto",
                "previous_override": True,
                "set_by": set_by,
                "reason": reason or "manual override cleared",
            },
        ))
        return {"ok": True, "mode": "auto", "tenant_id": tenant_id}

    if mode not in {m.value for m in IntakeMode}:
        return {"ok": False, "error": f"Invalid mode: {mode}. Use open/throttle/pause/auto."}

    event_log.upsert_intake_override(
        tenant_id=tenant, mode=mode,
        set_by=set_by, reason=reason,
    )
    event_log.append(Event(
        event_type=EventType.INTAKE_MODE_CHANGED,
        tenant_id=tenant_id,
        payload={
            "mode": mode,
            "set_by": set_by,
            "reason": reason or f"manual override to {mode}",
        },
    ))
    return {"ok": True, "mode": mode, "tenant_id": tenant_id}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _resolve_mode(
    *,
    tenant_id: str | None = None,
    config: dict[str, Any],
) -> tuple[IntakeMode, dict[str, Any]]:
    """Resolve current mode: manual override > auto-computed from health."""
    tenant = tenant_id or ""

    # Check manual override
    override = event_log.get_intake_override(tenant_id=tenant)
    if override is not None:
        auto_mode, signals = _compute_auto_mode(tenant_id=tenant_id, config=config)
        signals["auto_mode"] = auto_mode.value
        return IntakeMode(override["mode"]), signals

    mode, signals = _compute_auto_mode(tenant_id=tenant_id, config=config)
    signals["auto_mode"] = mode.value
    return mode, signals


def _compute_auto_mode(
    *,
    tenant_id: str | None = None,
    config: dict[str, Any],
) -> tuple[IntakeMode, dict[str, Any]]:
    """Derive intake mode from health + debt signals.

    Uses the worse of health score and inverse debt score to determine mode.
    This means high debt can trigger throttle/pause even if health looks OK.
    """
    health = projections.repo_health(tenant_id=tenant_id)
    queue = projections.queue_state(tenant_id=tenant_id)
    debt = projections.verification_debt(tenant_id=tenant_id)

    # Effective score: min(health, 100-debt) — debt drags the score down
    health_score = health.repo_health_score
    debt_adjusted = max(0.0, 100.0 - debt.debt_score)
    effective_score = min(health_score, debt_adjusted)

    pause_threshold = config.get("pause_below_health", DEFAULT_INTAKE_CONFIG["pause_below_health"])
    throttle_threshold = config.get("throttle_below_health", DEFAULT_INTAKE_CONFIG["throttle_below_health"])

    signals = {
        "health_score": health_score,
        "health_status": health.status,
        "debt_score": debt.debt_score,
        "debt_status": debt.status,
        "effective_score": round(effective_score, 1),
        "queue_total": queue.total,
        "queue_pending": len(queue.pending),
        "conflict_rate": health.conflict_rate,
        "pause_threshold": pause_threshold,
        "throttle_threshold": throttle_threshold,
    }

    if effective_score < pause_threshold:
        return IntakeMode.PAUSE, signals
    if effective_score < throttle_threshold:
        return IntakeMode.THROTTLE, signals
    return IntakeMode.OPEN, signals


def _throttle_bucket(intent_id: str) -> float:
    """Deterministic bucket [0.0, 1.0) for throttle decisions."""
    h = hashlib.sha256(intent_id.encode()).hexdigest()[:ROLLOUT_HASH_CHARS]
    return int(h, 16) / ROLLOUT_DIVISOR


def _emit_intake_event(
    intent: Intent,
    decision: IntakeDecision,
) -> None:
    """Emit the appropriate intake event."""
    if decision.accepted:
        etype = EventType.INTAKE_ACCEPTED
    elif decision.mode == IntakeMode.THROTTLE:
        etype = EventType.INTAKE_THROTTLED
    else:
        etype = EventType.INTAKE_REJECTED

    event_log.append(Event(
        event_type=etype,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=IntakePayload(
            mode=decision.mode.value,
            accepted=decision.accepted,
            risk_level=intent.risk_level.value,
            origin_type=intent.origin_type,
            signals=decision.signals,
            reason=decision.reason,
        ).to_dict(),
    ))


def _load_intake_config() -> dict[str, Any]:
    """Load intake config from policy config file."""
    from converge.policy import load_config as load_policy_config
    try:
        policy_cfg = load_policy_config()
        # intake section is an optional extension of the policy config file
        return getattr(policy_cfg, "intake", None) or DEFAULT_INTAKE_CONFIG
    except Exception:
        return dict(DEFAULT_INTAKE_CONFIG)
