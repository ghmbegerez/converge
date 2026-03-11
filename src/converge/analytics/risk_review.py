"""Risk review: comprehensive per-intent report."""

from __future__ import annotations

from typing import Any

from converge import event_log
from converge.models import EventType, now_iso

_REVIEW_RISK_THRESHOLD = 50
_REVIEW_CRITICAL_DISPLAY = 3
_DECISION_QUERY_LIMIT = 50


def risk_review(
    intent_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build comprehensive risk review for an intent."""
    from converge import projections

    intent = event_log.get_intent(intent_id)
    if intent is None:
        return {"error": f"Intent {intent_id} not found"}

    events = _gather_intent_events(intent_id)
    compliance = projections.compliance_report(tenant_id=tenant_id)
    diagnostics = _build_review_diagnostics(intent, events)

    review: dict[str, Any] = {
        "intent_id": intent_id,
        "intent": intent.to_dict(),
        "risk": events["risk_payload"],
        "simulation": events["sim_payload"],
        "policy": events["policy_payload"],
        "diagnostics": diagnostics,
        "compliance": compliance.to_dict(),
        "decision_history": [{"event_type": e["event_type"], "timestamp": e["timestamp"],
                               "payload": e["payload"]} for e in events["decisions"]],
        "timestamp": now_iso(),
        "tenant_id": tenant_id,
    }

    if events["risk_payload"]:
        review["learning"] = _derive_review_learning(events["risk_payload"], diagnostics, compliance)

    return review


def _gather_intent_events(intent_id: str) -> dict[str, Any]:
    """Gather latest risk/sim/policy/decision events for an intent."""
    risk_events = event_log.query(event_type=EventType.RISK_EVALUATED, intent_id=intent_id, limit=1)
    sim_events = event_log.query(event_type=EventType.SIMULATION_COMPLETED, intent_id=intent_id, limit=1)
    policy_events = event_log.query(event_type=EventType.POLICY_EVALUATED, intent_id=intent_id, limit=1)
    decisions = event_log.query(intent_id=intent_id, limit=_DECISION_QUERY_LIMIT)
    return {
        "risk_payload": risk_events[0]["payload"] if risk_events else None,
        "sim_payload": sim_events[0]["payload"] if sim_events else None,
        "policy_payload": policy_events[0]["payload"] if policy_events else None,
        "decisions": decisions,
    }


def _build_review_diagnostics(intent: Any, events: dict[str, Any]) -> list[dict[str, Any]]:
    """Build diagnostics from risk + simulation event payloads."""
    if not events["risk_payload"] or not events["sim_payload"]:
        return []

    from converge import risk as risk_mod
    from converge.models import RiskEval, Simulation

    re = events["risk_payload"]
    risk_eval = RiskEval(
        intent_id=intent.id,
        risk_score=re.get("risk_score", 0),
        damage_score=re.get("damage_score", 0),
        entropy_score=re.get("entropy_score", 0),
        propagation_score=re.get("propagation_score", 0),
        containment_score=re.get("containment_score", 0),
        findings=re.get("findings", []),
        impact_edges=re.get("impact_edges", []),
    )
    sp = events["sim_payload"]
    sim = Simulation(
        mergeable=sp.get("mergeable", True),
        conflicts=sp.get("conflicts", []),
        files_changed=sp.get("files_changed", []),
    )
    return risk_mod.build_diagnostics(intent, risk_eval, sim)


def _derive_review_learning(
    risk_data: dict,
    diagnostics: list[dict],
    compliance: Any,
) -> dict[str, Any]:
    lessons = []
    critical_diags = [d for d in diagnostics if d.get("severity") == "critical"]
    if critical_diags:
        lessons.append({
            "code": "learn.critical_diagnostics",
            "title": "Critical issues detected",
            "why": f"{len(critical_diags)} critical diagnostic(s) found",
            "action": "Address critical issues before proceeding: " +
                      "; ".join(d.get("explanation", "") for d in critical_diags[:_REVIEW_CRITICAL_DISPLAY]),
            "priority": 0,
        })
    risk_score = risk_data.get("risk_score", 0)
    if risk_score > _REVIEW_RISK_THRESHOLD:
        lessons.append({
            "code": "learn.review_risk",
            "title": "Elevated risk",
            "why": f"Risk score {risk_score:.0f}",
            "action": "Review impact graph and consider narrowing scope",
            "priority": 1,
        })
    return {"lessons": lessons, "summary": f"Review: {len(lessons)} actionable lesson(s)"}
