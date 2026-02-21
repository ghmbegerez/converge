"""Predictions: issue detection from recent trends + bomb signals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from converge import event_log
from converge.models import EventType
from converge.projections._time import _safe_avg, _since_hours

# --- Signal detection thresholds ---
_CONFLICT_RISE_DELTA = 0.1
_CONFLICT_MIN_SAMPLES = 3
_ENTROPY_SPIKE_MULT = 1.2
_ENTROPY_SPIKE_MIN = 15
_QUEUE_STALL_REQUEUE = 5
_REJECTION_RATE = 0.4
_REJECTION_MIN_DECISIONS = 3
_BOMB_PROPAGATION = 40
_BOMB_CASCADE_COUNT = 3
_BOMB_SPIRAL_CONT_DROP = 0.1
_BOMB_SPIRAL_CONT_ABS = 0.6
_THERMAL_ENTROPY = 20
_THERMAL_CONFLICT = 0.2
_THERMAL_PROPAGATION = 30

# --- Time windows ---
_WINDOW_24H = 24
_WINDOW_48H = 48
_QUERY_LIMIT = 10000
_BOMB_MIN_SAMPLES = 3


def predict_issues(
    db_path: str | Path,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Predict potential issues from recent trends."""
    since_24h = _since_hours(_WINDOW_24H)
    since_48h = _since_hours(_WINDOW_48H)

    # Pre-fetch event windows shared across detectors
    sims_24 = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, tenant_id=tenant_id, since=since_24h, limit=_QUERY_LIMIT)
    sims_48 = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, tenant_id=tenant_id, since=since_48h, limit=_QUERY_LIMIT)
    risk_24 = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, tenant_id=tenant_id, since=since_24h, limit=_QUERY_LIMIT)
    risk_48 = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, tenant_id=tenant_id, since=since_48h, limit=_QUERY_LIMIT)

    sims_prev = [s for s in sims_48 if s["timestamp"] < since_24h]
    risk_prev = [r for r in risk_48 if r["timestamp"] < since_24h]

    signals: list[dict[str, Any]] = []
    _detect_rising_conflicts(sims_24, sims_prev, signals)
    _detect_entropy_spike(risk_24, risk_prev, signals)
    _detect_queue_stalling(db_path, tenant_id, since_24h, signals)
    _detect_high_rejection(db_path, tenant_id, since_24h, signals)
    _detect_bomb_cascade(risk_24, signals)
    _detect_bomb_spiral(risk_24, risk_prev, signals)
    _detect_bomb_thermal(risk_24, sims_24, sims_prev, signals)

    return signals


def _detect_rising_conflicts(
    sims_24: list[dict], sims_prev: list[dict], out: list[dict[str, Any]],
) -> None:
    """Signal: conflict rate rising between periods."""
    conflict_rate_now = sum(1 for s in sims_24 if not s["payload"].get("mergeable")) / max(len(sims_24), 1)
    conflict_rate_prev = sum(1 for s in sims_prev if not s["payload"].get("mergeable")) / max(len(sims_prev), 1)
    if conflict_rate_now > conflict_rate_prev + _CONFLICT_RISE_DELTA and len(sims_24) > _CONFLICT_MIN_SAMPLES:
        out.append({
            "signal": "rising_conflict_rate",
            "severity": "high",
            "message": f"Conflict rate rose from {conflict_rate_prev:.0%} to {conflict_rate_now:.0%} in last 24h",
            "recommendation": "Consider pausing new intents and resolving current conflicts",
        })


def _detect_entropy_spike(
    risk_24: list[dict], risk_prev: list[dict], out: list[dict[str, Any]],
) -> None:
    """Signal: average entropy spiking."""
    avg_now = _safe_avg([r["payload"].get("entropy_score", 0) for r in risk_24])
    avg_prev = _safe_avg([r["payload"].get("entropy_score", 0) for r in risk_prev])
    if avg_now > avg_prev * _ENTROPY_SPIKE_MULT and avg_now > _ENTROPY_SPIKE_MIN and len(risk_24) > _CONFLICT_MIN_SAMPLES:
        out.append({
            "signal": "entropy_spike",
            "severity": "medium",
            "message": f"Average entropy rose from {avg_prev:.1f} to {avg_now:.1f}",
            "recommendation": "Review recent intents for large or high-risk changes",
        })


def _detect_queue_stalling(
    db_path: str | Path, tenant_id: str | None, since_24h: str, out: list[dict[str, Any]],
) -> None:
    """Signal: excessive requeues indicate queue stalling."""
    requeued = event_log.query(db_path, event_type=EventType.INTENT_REQUEUED, tenant_id=tenant_id, since=since_24h, limit=_QUERY_LIMIT)
    if len(requeued) > _QUEUE_STALL_REQUEUE:
        out.append({
            "signal": "queue_stalling",
            "severity": "high",
            "message": f"{len(requeued)} intents requeued in last 24h",
            "recommendation": "Check for systemic merge conflicts or failing checks",
        })


def _detect_high_rejection(
    db_path: str | Path, tenant_id: str | None, since_24h: str, out: list[dict[str, Any]],
) -> None:
    """Signal: high rejection rate relative to total decisions."""
    rejected = event_log.query(db_path, event_type=EventType.INTENT_REJECTED, tenant_id=tenant_id, since=since_24h, limit=_QUERY_LIMIT)
    merged = event_log.query(db_path, event_type=EventType.INTENT_MERGED, tenant_id=tenant_id, since=since_24h, limit=_QUERY_LIMIT)
    total = len(rejected) + len(merged)
    if total > _REJECTION_MIN_DECISIONS and len(rejected) / total > _REJECTION_RATE:
        out.append({
            "signal": "high_rejection_rate",
            "severity": "critical",
            "message": f"{len(rejected)}/{total} intents rejected in last 24h ({len(rejected)/total:.0%})",
            "recommendation": "Review policy thresholds or source branch quality",
        })


def _detect_bomb_cascade(risk_24: list[dict], out: list[dict[str, Any]]) -> None:
    """Bomb signal: multiple high-propagation changes detected."""
    if not risk_24:
        return
    high_prop = [r for r in risk_24 if r["payload"].get("propagation_score", 0) > _BOMB_PROPAGATION]
    if len(high_prop) >= _BOMB_CASCADE_COUNT:
        out.append({
            "signal": "bomb.cascade",
            "severity": "high",
            "message": f"{len(high_prop)}/{len(risk_24)} recent changes have high propagation scores (>40)",
            "recommendation": "Multiple high-blast-radius changes detected — risk of cascade failures",
        })


def _detect_bomb_spiral(
    risk_24: list[dict], risk_prev: list[dict], out: list[dict[str, Any]],
) -> None:
    """Bomb signal: containment scores trending downward."""
    if len(risk_24) < _BOMB_MIN_SAMPLES or len(risk_prev) < _BOMB_MIN_SAMPLES:
        return
    avg_cont_now = _safe_avg([r["payload"].get("containment_score", 1.0) for r in risk_24])
    avg_cont_prev = _safe_avg([r["payload"].get("containment_score", 1.0) for r in risk_prev])
    if avg_cont_now < avg_cont_prev - _BOMB_SPIRAL_CONT_DROP and avg_cont_now < _BOMB_SPIRAL_CONT_ABS:
        out.append({
            "signal": "bomb.spiral",
            "severity": "medium",
            "message": f"Containment dropping from {avg_cont_prev:.2f} to {avg_cont_now:.2f} — changes becoming less isolated",
            "recommendation": "Increasing cross-boundary coupling detected — enforce scope limits",
        })


def _detect_bomb_thermal(
    risk_24: list[dict], sims_24: list[dict], sims_prev: list[dict], out: list[dict[str, Any]],
) -> None:
    """Bomb signal: entropy, conflict rate, and propagation all elevated."""
    if not risk_24 or not sims_24:
        return
    avg_entropy = _safe_avg([r["payload"].get("entropy_score", 0) for r in risk_24])
    conflict_rate = sum(1 for s in sims_24 if not s["payload"].get("mergeable")) / max(len(sims_24), 1)
    avg_propagation = _safe_avg([r["payload"].get("propagation_score", 0) for r in risk_24])
    if avg_entropy > _THERMAL_ENTROPY and conflict_rate > _THERMAL_CONFLICT and avg_propagation > _THERMAL_PROPAGATION:
        out.append({
            "signal": "bomb.thermal_death",
            "severity": "critical",
            "message": f"System under thermal stress: entropy={avg_entropy:.1f}, "
                       f"conflict_rate={conflict_rate:.0%}, propagation elevated",
            "recommendation": "Halt new intents — system entropy is approaching critical levels",
        })
