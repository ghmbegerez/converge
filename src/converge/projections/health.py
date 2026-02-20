"""Health projections: repo health, change health, predictive health gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from converge import event_log
from converge.models import EventType, HealthSnapshot, now_iso
from converge.projections._time import _since_days, _since_hours
from converge.projections.learning import derive_change_learning, derive_health_learning


def repo_health(
    db_path: str | Path,
    tenant_id: str | None = None,
    window_hours: int = 24,
) -> HealthSnapshot:
    """Compute repo health from recent events."""
    since = _since_hours(window_hours)

    sims = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, tenant_id=tenant_id, since=since, limit=10000)
    total_sims = len(sims)
    mergeable_sims = sum(1 for s in sims if s["payload"].get("mergeable"))
    mergeable_rate = (mergeable_sims / total_sims) if total_sims > 0 else 1.0
    conflict_rate = 1.0 - mergeable_rate

    merged = event_log.query(db_path, event_type=EventType.INTENT_MERGED, tenant_id=tenant_id, since=since, limit=10000)
    rejected = event_log.query(db_path, event_type=EventType.INTENT_REJECTED, tenant_id=tenant_id, since=since, limit=10000)

    risk_events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, tenant_id=tenant_id, since=since, limit=10000)
    avg_entropy = 0.0
    if risk_events:
        avg_entropy = sum(e["payload"].get("entropy_score", 0) for e in risk_events) / len(risk_events)

    active = event_log.list_intents(db_path, tenant_id=tenant_id, limit=10000)
    active_count = sum(1 for i in active if i.status.value in ("READY", "VALIDATED", "QUEUED"))

    # Compute health score: 100 = perfect, 0 = critical
    health_score = 100.0
    health_score -= conflict_rate * 30
    health_score -= min(avg_entropy, 50) * 0.5
    health_score -= min(len(rejected), 20) * 1.5
    health_score = max(0.0, round(health_score, 1))

    if health_score >= 70:
        status = "green"
    elif health_score >= 40:
        status = "yellow"
    else:
        status = "red"

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

    event_log.append(db_path, event_log.Event(
        event_type=EventType.HEALTH_SNAPSHOT,
        tenant_id=tenant_id,
        payload=snapshot.to_dict(),
    ))

    return snapshot


def change_health(
    db_path: str | Path,
    intent_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Compute health for a specific change/intent."""
    risk_events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, intent_id=intent_id, limit=1)
    sim_events = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, intent_id=intent_id, limit=1)
    policy_events = event_log.query(db_path, event_type=EventType.POLICY_EVALUATED, intent_id=intent_id, limit=1)

    risk_score = risk_events[0]["payload"].get("risk_score", 0) if risk_events else 0
    entropy = risk_events[0]["payload"].get("entropy_score", 0) if risk_events else 0
    mergeable = sim_events[0]["payload"].get("mergeable", True) if sim_events else True
    verdict = policy_events[0]["payload"].get("verdict", "unknown") if policy_events else "unknown"

    health_score = 100.0 - risk_score * 0.5 - entropy * 0.3 - (0 if mergeable else 30)
    health_score = max(0.0, round(health_score, 1))

    result = {
        "intent_id": intent_id,
        "health_score": health_score,
        "risk_score": risk_score,
        "entropy_score": entropy,
        "mergeable": mergeable,
        "policy_verdict": verdict,
        "status": "green" if health_score >= 70 else ("yellow" if health_score >= 40 else "red"),
        "timestamp": now_iso(),
        "tenant_id": tenant_id,
        "learning": derive_change_learning(health_score, risk_score, entropy, mergeable),
    }

    event_log.append(db_path, event_log.Event(
        event_type=EventType.HEALTH_CHANGE_SNAPSHOT,
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload=result,
    ))
    return result


def predict_health(
    db_path: str | Path,
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
    snapshots = event_log.query(db_path, event_type=EventType.HEALTH_SNAPSHOT, tenant_id=tenant_id, since=since, limit=500)
    # Sort ascending (oldest first) for time-series analysis
    snapshots.sort(key=lambda s: s["timestamp"])

    if len(snapshots) < min_snapshots:
        return {
            "projected_status": "unknown",
            "confidence": "low",
            "reason": f"Not enough data ({len(snapshots)} snapshots, need {min_snapshots}+)",
            "recommendation": "Collect more health snapshots before prediction is reliable",
            "should_gate": False,
        }

    # Extract time series
    scores = [s["payload"].get("repo_health_score", 100.0) for s in snapshots]
    entropies = [s["payload"].get("entropy_score", 0.0) for s in snapshots]
    conflict_rates = [s["payload"].get("conflict_rate", 0.0) for s in snapshots]

    # Split into recent half and older half
    mid = len(scores) // 2
    older_scores = scores[:mid] if mid > 0 else scores
    recent_scores = scores[mid:] if mid > 0 else scores
    older_entropy = entropies[:mid] if mid > 0 else entropies
    recent_entropy = entropies[mid:] if mid > 0 else entropies
    older_conflict = conflict_rates[:mid] if mid > 0 else conflict_rates
    recent_conflict = conflict_rates[mid:] if mid > 0 else conflict_rates

    avg_recent = sum(recent_scores) / max(len(recent_scores), 1)
    avg_older = sum(older_scores) / max(len(older_scores), 1)
    avg_entropy_recent = sum(recent_entropy) / max(len(recent_entropy), 1)
    avg_entropy_older = sum(older_entropy) / max(len(older_entropy), 1)
    avg_conflict_recent = sum(recent_conflict) / max(len(recent_conflict), 1)
    avg_conflict_older = sum(older_conflict) / max(len(older_conflict), 1)

    # Compute velocity (change per period)
    health_velocity = avg_recent - avg_older
    entropy_velocity = avg_entropy_recent - avg_entropy_older
    conflict_velocity = avg_conflict_recent - avg_conflict_older

    # Project forward
    projected_health = max(0.0, min(100.0, avg_recent + health_velocity))
    projected_entropy = max(0.0, avg_entropy_recent + entropy_velocity)

    # Determine projected status
    if projected_health >= 70:
        projected_status = "green"
    elif projected_health >= 40:
        projected_status = "yellow"
    else:
        projected_status = "red"

    # Current status
    current_health = scores[-1] if scores else 100.0
    if current_health >= 70:
        current_status = "green"
    elif current_health >= 40:
        current_status = "yellow"
    else:
        current_status = "red"

    # Should we gate? Block if trajectory goes from green→red or yellow→red
    signals = []
    should_gate = False

    if health_velocity < -5:
        signals.append({
            "signal": "predict.health_falling",
            "message": f"Health declining at {health_velocity:.1f} per period (current: {avg_recent:.0f})",
            "severity": "high" if health_velocity < -10 else "medium",
        })
    if entropy_velocity > 3:
        signals.append({
            "signal": "predict.entropy_rising",
            "message": f"Entropy rising at +{entropy_velocity:.1f} per period (current: {avg_entropy_recent:.1f})",
            "severity": "high" if entropy_velocity > 5 else "medium",
        })
    if conflict_velocity > 0.05:
        signals.append({
            "signal": "predict.conflict_rising",
            "message": f"Conflict rate rising at +{conflict_velocity:.2f} per period (current: {avg_conflict_recent:.1%})",
            "severity": "medium",
        })

    if projected_status == "red" and current_status != "red":
        should_gate = True
        signals.append({
            "signal": "predict.approaching_red",
            "message": f"Current: {current_status} ({current_health:.0f}), projected: red ({projected_health:.0f})",
            "severity": "critical",
        })

    recommendation = "System trajectory is stable" if not should_gate else \
        "Consider pausing new intents — health trajectory indicates degradation"

    result = {
        "current_status": current_status,
        "current_health": round(current_health, 1),
        "projected_status": projected_status,
        "projected_health": round(projected_health, 1),
        "horizon_days": horizon_days,
        "velocity": {
            "health": round(health_velocity, 2),
            "entropy": round(entropy_velocity, 2),
            "conflict_rate": round(conflict_velocity, 4),
        },
        "signals": signals,
        "should_gate": should_gate,
        "confidence": "high" if len(snapshots) >= 7 else "medium",
        "recommendation": recommendation,
        "data_points": len(snapshots),
        "timestamp": now_iso(),
        "tenant_id": tenant_id,
    }

    event_log.append(db_path, event_log.Event(
        event_type=EventType.HEALTH_PREDICTION,
        tenant_id=tenant_id,
        payload=result,
    ))

    return result
