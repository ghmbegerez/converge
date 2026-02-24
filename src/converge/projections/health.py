"""Health projections: repo health, change health, predictive health gate."""

from __future__ import annotations

from typing import Any

from converge import event_log
from converge.defaults import QUERY_LIMIT_LARGE, QUERY_LIMIT_MEDIUM
from converge.models import EventType, Status, now_iso
from converge.projections_models import HealthSnapshot
from converge.projections._time import _safe_avg, _since_days, _since_hours
from converge.projections.learning import derive_change_learning, derive_health_learning

# --- Health scoring constants ---
_HEALTH_GREEN = 70
_HEALTH_YELLOW = 40
_W_CONFLICT = 30        # weight: conflict_rate impact on health
_W_ENTROPY_CAP = 50     # cap for avg_entropy before weighting
_W_ENTROPY = 0.5        # weight: entropy impact on health
_W_REJECTED_CAP = 20    # cap for rejected count before weighting
_W_REJECTED = 1.5       # weight: rejection impact on health
_W_CHANGE_RISK = 0.5    # weight: risk_score impact on change health
_W_CHANGE_ENTROPY = 0.3 # weight: entropy impact on change health
_W_CHANGE_CONFLICT = 30  # penalty if not mergeable
_HIGH_CONFIDENCE_SNAPSHOTS = 7

# --- Prediction velocity thresholds ---
_PREDICT_HEALTH_DECLINE_MED = -5    # health velocity below this → medium signal
_PREDICT_HEALTH_DECLINE_HIGH = -10  # health velocity below this → high signal
_PREDICT_ENTROPY_RISE_MED = 3       # entropy velocity above this → medium signal
_PREDICT_ENTROPY_RISE_HIGH = 5      # entropy velocity above this → high signal
_PREDICT_CONFLICT_RISE = 0.05       # conflict velocity above this → signal


def _health_status(score: float) -> str:
    """Map health score to green/yellow/red."""
    if score >= _HEALTH_GREEN:
        return "green"
    if score >= _HEALTH_YELLOW:
        return "yellow"
    return "red"


def repo_health(
    tenant_id: str | None = None,
    window_hours: int = 24,
) -> HealthSnapshot:
    """Compute repo health from recent events."""
    since = _since_hours(window_hours)

    sims = event_log.query(event_type=EventType.SIMULATION_COMPLETED, tenant_id=tenant_id, since=since, limit=QUERY_LIMIT_LARGE)
    total_sims = len(sims)
    mergeable_sims = sum(1 for s in sims if s["payload"].get("mergeable"))
    mergeable_rate = (mergeable_sims / total_sims) if total_sims > 0 else 1.0
    conflict_rate = 1.0 - mergeable_rate

    merged = event_log.query(event_type=EventType.INTENT_MERGED, tenant_id=tenant_id, since=since, limit=QUERY_LIMIT_LARGE)
    rejected = event_log.query(event_type=EventType.INTENT_REJECTED, tenant_id=tenant_id, since=since, limit=QUERY_LIMIT_LARGE)

    risk_events = event_log.query(event_type=EventType.RISK_EVALUATED, tenant_id=tenant_id, since=since, limit=QUERY_LIMIT_LARGE)
    avg_entropy = 0.0
    if risk_events:
        avg_entropy = sum(e["payload"].get("entropy_score", 0) for e in risk_events) / len(risk_events)

    active = event_log.list_intents(tenant_id=tenant_id, limit=QUERY_LIMIT_LARGE)
    active_count = sum(1 for i in active if i.status in (Status.READY, Status.VALIDATED, Status.QUEUED))

    # Compute health score: 100 = perfect, 0 = critical
    health_score = 100.0
    health_score -= conflict_rate * _W_CONFLICT
    health_score -= min(avg_entropy, _W_ENTROPY_CAP) * _W_ENTROPY
    health_score -= min(len(rejected), _W_REJECTED_CAP) * _W_REJECTED
    health_score = max(0.0, round(health_score, 1))

    status = _health_status(health_score)

    snapshot = HealthSnapshot(
        repo_health_score=health_score,
        entropy_score=round(avg_entropy, 1),
        mergeable_rate=round(mergeable_rate, 3),
        conflict_rate=round(conflict_rate, 3),
        active_intents=active_count,
        merged_last_24h=len(merged),
        rejected_last_24h=len(rejected),
        status=status,
        tenant_id=tenant_id,
        learning=derive_health_learning(health_score, mergeable_rate, avg_entropy, len(rejected)),
    )

    event_log.append(event_log.Event(
        event_type=EventType.HEALTH_SNAPSHOT,
        tenant_id=tenant_id,
        payload=snapshot.to_dict(),
    ))

    return snapshot


def change_health(
    intent_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Compute health for a specific change/intent."""
    risk_events = event_log.query(event_type=EventType.RISK_EVALUATED, intent_id=intent_id, limit=1)
    sim_events = event_log.query(event_type=EventType.SIMULATION_COMPLETED, intent_id=intent_id, limit=1)
    policy_events = event_log.query(event_type=EventType.POLICY_EVALUATED, intent_id=intent_id, limit=1)

    risk_score = risk_events[0]["payload"].get("risk_score", 0) if risk_events else 0
    entropy = risk_events[0]["payload"].get("entropy_score", 0) if risk_events else 0
    mergeable = sim_events[0]["payload"].get("mergeable", True) if sim_events else True
    verdict = policy_events[0]["payload"].get("verdict", "unknown") if policy_events else "unknown"

    health_score = 100.0 - risk_score * _W_CHANGE_RISK - entropy * _W_CHANGE_ENTROPY - (0 if mergeable else _W_CHANGE_CONFLICT)
    health_score = max(0.0, round(health_score, 1))

    result = {
        "intent_id": intent_id,
        "health_score": health_score,
        "risk_score": risk_score,
        "entropy_score": entropy,
        "mergeable": mergeable,
        "policy_verdict": verdict,
        "status": _health_status(health_score),
        "timestamp": now_iso(),
        "tenant_id": tenant_id,
        "learning": derive_change_learning(health_score, risk_score, entropy, mergeable),
    }

    event_log.append(event_log.Event(
        event_type=EventType.HEALTH_CHANGE_SNAPSHOT,
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload=result,
    ))
    return result


def predict_health(
    tenant_id: str | None = None,
    horizon_days: int = 7,
    min_snapshots: int = 3,
) -> dict[str, Any]:
    """Forward-looking health projection.

    Analyzes health trend over recent days and projects where the system
    will be in `horizon_days`. Can recommend blocking NOW if trajectory
    indicates red in the near future, even if current state is green.
    """
    since = _since_days(horizon_days * 2)
    snapshots = event_log.query(event_type=EventType.HEALTH_SNAPSHOT, tenant_id=tenant_id, since=since, limit=QUERY_LIMIT_MEDIUM)
    snapshots.sort(key=lambda s: s["timestamp"])

    if len(snapshots) < min_snapshots:
        return {
            "projected_status": "unknown",
            "confidence": "low",
            "reason": f"Not enough data ({len(snapshots)} snapshots, need {min_snapshots}+)",
            "recommendation": "Collect more health snapshots before prediction is reliable",
            "should_gate": False,
        }

    velocity = _compute_velocities(snapshots)
    current_health = velocity["current_health"]
    projected_health = velocity["projected_health"]
    current_status = _health_status(current_health)
    projected_status = _health_status(projected_health)

    signals, should_gate = _detect_health_signals(velocity, current_status, current_health, projected_status, projected_health)

    result = _build_prediction_result(
        velocity, current_health, projected_health,
        horizon_days, signals, should_gate,
        len(snapshots), tenant_id,
    )

    event_log.append(event_log.Event(
        event_type=EventType.HEALTH_PREDICTION,
        tenant_id=tenant_id,
        payload=result,
    ))

    return result


def _split_halves(data: list[float]) -> tuple[list[float], list[float]]:
    """Split a time-series into older and recent halves."""
    mid = len(data) // 2
    if mid == 0:
        return data, data
    return data[:mid], data[mid:]


def _compute_velocities(snapshots: list[dict[str, Any]]) -> dict[str, float]:
    """Compute health/entropy/conflict velocities from time-series snapshots."""
    scores = [s["payload"].get("repo_health_score", 100.0) for s in snapshots]
    entropies = [s["payload"].get("entropy_score", 0.0) for s in snapshots]
    conflict_rates = [s["payload"].get("conflict_rate", 0.0) for s in snapshots]

    older_scores, recent_scores = _split_halves(scores)
    older_entropy, recent_entropy = _split_halves(entropies)
    older_conflict, recent_conflict = _split_halves(conflict_rates)

    avg_recent = _safe_avg(recent_scores)
    avg_older = _safe_avg(older_scores)
    avg_entropy_recent = _safe_avg(recent_entropy)
    avg_entropy_older = _safe_avg(older_entropy)
    avg_conflict_recent = _safe_avg(recent_conflict)
    avg_conflict_older = _safe_avg(older_conflict)

    health_velocity = avg_recent - avg_older
    entropy_velocity = avg_entropy_recent - avg_entropy_older
    conflict_velocity = avg_conflict_recent - avg_conflict_older

    return {
        "health_velocity": health_velocity,
        "entropy_velocity": entropy_velocity,
        "conflict_velocity": conflict_velocity,
        "avg_recent": avg_recent,
        "avg_entropy_recent": avg_entropy_recent,
        "avg_conflict_recent": avg_conflict_recent,
        "current_health": scores[-1] if scores else 100.0,
        "projected_health": max(0.0, min(100.0, avg_recent + health_velocity)),
    }


def _detect_health_signals(
    velocity: dict[str, float],
    current_status: str,
    current_health: float,
    projected_status: str,
    projected_health: float,
) -> tuple[list[dict[str, Any]], bool]:
    """Detect prediction signals from velocity data. Returns (signals, should_gate)."""
    signals: list[dict[str, Any]] = []
    hv = velocity["health_velocity"]
    ev = velocity["entropy_velocity"]
    cv = velocity["conflict_velocity"]

    if hv < _PREDICT_HEALTH_DECLINE_MED:
        signals.append({
            "signal": "predict.health_falling",
            "message": f"Health declining at {hv:.1f} per period (current: {velocity['avg_recent']:.0f})",
            "severity": "high" if hv < _PREDICT_HEALTH_DECLINE_HIGH else "medium",
        })
    if ev > _PREDICT_ENTROPY_RISE_MED:
        signals.append({
            "signal": "predict.entropy_rising",
            "message": f"Entropy rising at +{ev:.1f} per period (current: {velocity['avg_entropy_recent']:.1f})",
            "severity": "high" if ev > _PREDICT_ENTROPY_RISE_HIGH else "medium",
        })
    if cv > _PREDICT_CONFLICT_RISE:
        signals.append({
            "signal": "predict.conflict_rising",
            "message": f"Conflict rate rising at +{cv:.2f} per period (current: {velocity['avg_conflict_recent']:.1%})",
            "severity": "medium",
        })

    should_gate = False
    if projected_status == "red" and current_status != "red":
        should_gate = True
        signals.append({
            "signal": "predict.approaching_red",
            "message": f"Current: {current_status} ({current_health:.0f}), projected: red ({projected_health:.0f})",
            "severity": "critical",
        })

    return signals, should_gate


def _build_prediction_result(
    velocity: dict[str, float],
    current_health: float,
    projected_health: float,
    horizon_days: int,
    signals: list[dict[str, Any]],
    should_gate: bool,
    data_points: int,
    tenant_id: str | None,
) -> dict[str, Any]:
    """Assemble the final prediction result dict."""
    return {
        "current_status": _health_status(current_health),
        "current_health": round(current_health, 1),
        "projected_status": _health_status(projected_health),
        "projected_health": round(projected_health, 1),
        "horizon_days": horizon_days,
        "velocity": {
            "health": round(velocity["health_velocity"], 2),
            "entropy": round(velocity["entropy_velocity"], 2),
            "conflict_rate": round(velocity["conflict_velocity"], 4),
        },
        "signals": signals,
        "should_gate": should_gate,
        "confidence": "high" if data_points >= _HIGH_CONFIDENCE_SNAPSHOTS else "medium",
        "recommendation": "System trajectory is stable" if not should_gate else
            "Consider pausing new intents — health trajectory indicates degradation",
        "data_points": data_points,
        "timestamp": now_iso(),
        "tenant_id": tenant_id,
    }
