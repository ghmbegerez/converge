"""Predictions: issue detection from recent trends + bomb signals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from converge import event_log
from converge.models import EventType
from converge.projections._time import _since_hours


def predict_issues(
    db_path: str | Path,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Predict potential issues from recent trends."""
    signals = []
    since_24h = _since_hours(24)
    since_48h = _since_hours(48)

    # Signal: rising conflict rate
    sims_24 = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, tenant_id=tenant_id, since=since_24h, limit=10000)
    sims_48 = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, tenant_id=tenant_id, since=since_48h, limit=10000)
    sims_prev = [s for s in sims_48 if s["timestamp"] < since_24h]

    conflict_rate_now = (sum(1 for s in sims_24 if not s["payload"].get("mergeable")) / max(len(sims_24), 1))
    conflict_rate_prev = (sum(1 for s in sims_prev if not s["payload"].get("mergeable")) / max(len(sims_prev), 1))

    if conflict_rate_now > conflict_rate_prev + 0.1 and len(sims_24) > 3:
        signals.append({
            "signal": "rising_conflict_rate",
            "severity": "high",
            "message": f"Conflict rate rose from {conflict_rate_prev:.0%} to {conflict_rate_now:.0%} in last 24h",
            "recommendation": "Consider pausing new intents and resolving current conflicts",
        })

    # Signal: entropy spike
    risk_24 = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, tenant_id=tenant_id, since=since_24h, limit=10000)
    risk_prev = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, tenant_id=tenant_id, since=since_48h, limit=10000)
    risk_prev = [r for r in risk_prev if r["timestamp"] < since_24h]

    avg_entropy_now = sum(r["payload"].get("entropy_score", 0) for r in risk_24) / max(len(risk_24), 1)
    avg_entropy_prev = sum(r["payload"].get("entropy_score", 0) for r in risk_prev) / max(len(risk_prev), 1)

    if avg_entropy_now > avg_entropy_prev * 1.2 and avg_entropy_now > 15 and len(risk_24) > 3:
        signals.append({
            "signal": "entropy_spike",
            "severity": "medium",
            "message": f"Average entropy rose from {avg_entropy_prev:.1f} to {avg_entropy_now:.1f}",
            "recommendation": "Review recent intents for large or high-risk changes",
        })

    # Signal: queue stalling
    requeued = event_log.query(db_path, event_type=EventType.INTENT_REQUEUED, tenant_id=tenant_id, since=since_24h, limit=10000)
    if len(requeued) > 5:
        signals.append({
            "signal": "queue_stalling",
            "severity": "high",
            "message": f"{len(requeued)} intents requeued in last 24h",
            "recommendation": "Check for systemic merge conflicts or failing checks",
        })

    # Signal: high rejection rate
    rejected_24 = event_log.query(db_path, event_type=EventType.INTENT_REJECTED, tenant_id=tenant_id, since=since_24h, limit=10000)
    merged_24 = event_log.query(db_path, event_type=EventType.INTENT_MERGED, tenant_id=tenant_id, since=since_24h, limit=10000)
    total_decisions = len(rejected_24) + len(merged_24)
    if total_decisions > 3 and len(rejected_24) / total_decisions > 0.4:
        signals.append({
            "signal": "high_rejection_rate",
            "severity": "critical",
            "message": f"{len(rejected_24)}/{total_decisions} intents rejected in last 24h ({len(rejected_24)/total_decisions:.0%})",
            "recommendation": "Review policy thresholds or source branch quality",
        })

    # ---------------------------------------------------------------
    # Structural degradation signals (bombs) from risk events
    # ---------------------------------------------------------------

    # Bomb: cascade — multiple recent risk events show high propagation
    if risk_24:
        high_prop = [r for r in risk_24 if r["payload"].get("propagation_score", 0) > 40]
        if len(high_prop) >= 3:
            signals.append({
                "signal": "bomb.cascade",
                "severity": "high",
                "message": f"{len(high_prop)}/{len(risk_24)} recent changes have high propagation scores (>40)",
                "recommendation": "Multiple high-blast-radius changes detected — risk of cascade failures",
            })

    # Bomb: spiral — containment scores trending downward
    if len(risk_24) >= 3 and len(risk_prev) >= 3:
        avg_cont_now = sum(r["payload"].get("containment_score", 1.0) for r in risk_24) / len(risk_24)
        avg_cont_prev = sum(r["payload"].get("containment_score", 1.0) for r in risk_prev) / len(risk_prev)
        if avg_cont_now < avg_cont_prev - 0.1 and avg_cont_now < 0.6:
            signals.append({
                "signal": "bomb.spiral",
                "severity": "medium",
                "message": f"Containment dropping from {avg_cont_prev:.2f} to {avg_cont_now:.2f} — changes becoming less isolated",
                "recommendation": "Increasing cross-boundary coupling detected — enforce scope limits",
            })

    # Bomb: thermal_death — entropy, conflict rate, and propagation all elevated
    if risk_24 and sims_24:
        all_hot = (
            avg_entropy_now > 20 and
            conflict_rate_now > 0.2 and
            sum(r["payload"].get("propagation_score", 0) for r in risk_24) / len(risk_24) > 30
        )
        if all_hot:
            signals.append({
                "signal": "bomb.thermal_death",
                "severity": "critical",
                "message": f"System under thermal stress: entropy={avg_entropy_now:.1f}, "
                           f"conflict_rate={conflict_rate_now:.0%}, propagation elevated",
                "recommendation": "Halt new intents — system entropy is approaching critical levels",
            })

    return signals
