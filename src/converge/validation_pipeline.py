"""Validation pipeline: extracted from engine.py.

Implements the full validation flow for an intent:
  simulate -> checks -> risk -> coherence -> policy -> risk gate -> finalize

Each step returns a StepResult = tuple[value, blocked_dict_or_None].
If blocked is not None the pipeline short-circuits.

This module is imported by engine.validate_intent() which acts as the
public entry point.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from converge.models import (
    CoherenceEvaluation,
    Event,
    EventType,
    Intent,
    PolicyEvaluation,
    PolicyVerdict,
    RiskEval,
    Simulation,
    Status,
)
from converge import analytics, coherence, event_log, policy, reviews, risk
from converge.defaults import CONFLICT_DISPLAY_LIMIT
from converge.event_payloads import (
    BlockPayload,
    GatePayload,
    PolicyPayload,
)

# ---------------------------------------------------------------------------
# Type alias: every step returns (value, blocked_or_None)
# ---------------------------------------------------------------------------

StepResult = tuple[dict, bool]


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------

def run_validation_pipeline(
    intent: Intent,
    *,
    sim: Simulation | None = None,
    use_last_simulation: bool = False,
    skip_checks: bool = False,
    config: policy.PolicyConfig | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """
    Full validation of an intent: simulate, check, evaluate risk, evaluate policy.
    Returns decision dict and updates intent status.

    This is where Invariant 1 lives:
      mergeable(i, t) = can_merge(M(t), Delta_i) AND checks_pass
    """
    # Import engine lazily to avoid circular imports -- we only need
    # simulate / simulate_from_last / run_checks / checks_for_risk_level
    from converge import engine as _engine

    cfg = config or policy.load_config()
    trace_id = event_log.fresh_trace_id()
    log.info("validation pipeline start", extra={"intent_id": intent.id, "trace_id": trace_id})

    sim, blocked = _resolve_simulation(intent, sim, use_last_simulation, cwd, trace_id,
                                       _engine=_engine)
    if blocked:
        return blocked

    checks_passed, blocked = _run_validation_checks(intent, cfg, skip_checks, sim, trace_id,
                                                    cwd=cwd, _engine=_engine)
    if blocked:
        return blocked

    risk_eval = _evaluate_risk_step(intent, sim, cwd, trace_id)

    coherence_eval, blocked = _evaluate_coherence_step(intent, risk_eval, cwd, trace_id)
    if blocked:
        return blocked

    policy_eval, blocked = _evaluate_policy_step(
        intent, checks_passed, risk_eval, cfg, sim, trace_id,
        coherence_eval=coherence_eval,
    )
    if blocked:
        return blocked

    risk_gate, blocked = _evaluate_risk_gate_step(intent, risk_eval, policy_eval, sim, trace_id)
    if blocked:
        return blocked

    return _finalize_validation(intent, sim, risk_eval, policy_eval, risk_gate, trace_id,
                                coherence_eval=coherence_eval)


# ---------------------------------------------------------------------------
# Step 1: Resolve or run simulation
# ---------------------------------------------------------------------------

def _resolve_simulation(
    intent: Intent,
    sim: Simulation | None,
    use_last_simulation: bool,
    cwd: str | Path | None,
    trace_id: str,
    *,
    _engine: Any = None,
) -> tuple[Simulation | None, dict[str, Any] | None]:
    """Step 1: Resolve or run simulation."""
    log.info("step 1: resolve simulation", extra={"intent_id": intent.id, "step": "simulation"})
    if _engine is None:
        from converge import engine as _engine

    if sim is None:
        if use_last_simulation:
            sim = _engine.simulate_from_last(intent.id)
            if sim is None:
                return None, block_intent(intent, "No previous simulation found", trace_id=trace_id)
        else:
            sim = _engine.simulate(intent.source, intent.target,
                                   intent_id=intent.id, tenant_id=intent.tenant_id, cwd=cwd,
                                   trace_id=trace_id)

    if not sim.mergeable:
        return None, block_intent(intent, f"Merge conflicts: {', '.join(sim.conflicts[:CONFLICT_DISPLAY_LIMIT])}",
                            sim=sim, trace_id=trace_id)
    return sim, None


# ---------------------------------------------------------------------------
# Step 2: Execute checks
# ---------------------------------------------------------------------------

def _run_validation_checks(
    intent: Intent,
    cfg: policy.PolicyConfig,
    skip_checks: bool,
    sim: Simulation,
    trace_id: str,
    cwd: str | Path | None = None,
    *,
    _engine: Any = None,
) -> tuple[list[str] | None, dict[str, Any] | None]:
    """Step 2: Execute checks."""
    log.info("step 2: run checks (skip=%s)", skip_checks, extra={"intent_id": intent.id, "step": "checks"})
    if _engine is None:
        from converge import engine as _engine

    if not skip_checks:
        required = _engine.checks_for_risk_level(intent.risk_level, cfg)
        results = _engine.run_checks(required, intent_id=intent.id,
                                     tenant_id=intent.tenant_id, cwd=cwd,
                                     trace_id=trace_id)
        checks_passed = [r.check_type for r in results if r.passed]
        failed = [r for r in results if not r.passed]
        if failed:
            names = [r.check_type for r in failed]
            return None, block_intent(intent, f"Checks failed: {names}",
                                sim=sim, trace_id=trace_id)
        return checks_passed, None

    return _engine.checks_for_risk_level(intent.risk_level, cfg), None


# ---------------------------------------------------------------------------
# Step 3: Evaluate risk (never blocks -- informational)
# ---------------------------------------------------------------------------

def _evaluate_risk_step(
    intent: Intent,
    sim: Simulation,
    cwd: str | Path | None,
    trace_id: str,
) -> RiskEval:
    """Step 3: Evaluate risk (never blocks -- informational)."""
    log.info("step 3: evaluate risk", extra={"intent_id": intent.id, "step": "risk"})
    coupling_data = analytics.load_coupling_data(cwd=cwd)
    risk_eval = risk.evaluate_risk(intent, sim, coupling_data=coupling_data)

    event_log.append(Event(
        event_type=EventType.RISK_EVALUATED,
        trace_id=trace_id,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=risk_eval.to_dict(),
        evidence={
            "risk_score": risk_eval.risk_score,
            "damage_score": risk_eval.damage_score,
            "signals": {
                "entropic_load": risk_eval.entropic_load,
                "contextual_value": risk_eval.contextual_value,
                "complexity_delta": risk_eval.complexity_delta,
                "path_dependence": risk_eval.path_dependence,
            },
            "bombs": [b["type"] for b in risk_eval.bombs],
            "trace_id": trace_id,
        },
    ))

    # Initiative 2: Auto-reclassify risk level from scores
    from converge.feature_flags import is_enabled
    if is_enabled("risk_auto_classify"):
        from converge.risk.eval import classify_risk_level

        new_level = classify_risk_level(risk_eval.risk_score)
        if new_level != intent.risk_level:
            old_level = intent.risk_level
            intent.risk_level = new_level
            event_log.update_intent_status(intent.id, intent.status)
            event_log.append(Event(
                event_type=EventType.RISK_LEVEL_RECLASSIFIED,
                intent_id=intent.id,
                trace_id=trace_id,
                tenant_id=intent.tenant_id,
                payload={
                    "old": old_level.value,
                    "new": new_level.value,
                    "risk_score": risk_eval.risk_score,
                },
            ))

    log.info(
        "step 3: risk score=%.2f", risk_eval.risk_score,
        extra={"intent_id": intent.id, "step": "risk", "risk_score": risk_eval.risk_score},
    )
    return risk_eval


# ---------------------------------------------------------------------------
# Step 3.5: Evaluate coherence
# ---------------------------------------------------------------------------

def _evaluate_coherence_step(
    intent: Intent,
    risk_eval: RiskEval,
    cwd: str | Path | None,
    trace_id: str,
) -> tuple[CoherenceEvaluation | None, dict[str, Any] | None]:
    """Step 3.5: Evaluate coherence questions against the working tree."""
    log.info("step 3.5: evaluate coherence", extra={"intent_id": intent.id, "step": "coherence"})
    questions = coherence.load_questions()
    if not questions:
        # No coherence harness configured -- pass automatically (backward compatible)
        return CoherenceEvaluation(
            coherence_score=100.0, verdict="pass", results=[],
            harness_version="none",
        ), None

    coherence_eval = coherence.evaluate(questions, workdir=cwd)

    # Cross-check vs risk (consistency validation)
    coherence_eval.inconsistencies = coherence.check_consistency(coherence_eval, risk_eval)

    # Apply inconsistency adjustments
    if coherence_eval.inconsistencies:
        # If in warn zone and has inconsistencies -> downgrade to fail
        if coherence_eval.verdict == "warn":
            coherence_eval = CoherenceEvaluation(
                coherence_score=coherence_eval.coherence_score,
                verdict="fail",
                results=coherence_eval.results,
                harness_version=coherence_eval.harness_version,
                inconsistencies=coherence_eval.inconsistencies,
            )
        # If passed but has inconsistencies -> downgrade to warn
        elif coherence_eval.verdict == "pass":
            coherence_eval = CoherenceEvaluation(
                coherence_score=coherence_eval.coherence_score,
                verdict="warn",
                results=coherence_eval.results,
                harness_version=coherence_eval.harness_version,
                inconsistencies=coherence_eval.inconsistencies,
            )

        # Emit inconsistency event
        event_log.append(Event(
            event_type=EventType.COHERENCE_INCONSISTENCY,
            trace_id=trace_id,
            intent_id=intent.id,
            tenant_id=intent.tenant_id,
            payload={
                "inconsistencies": coherence_eval.inconsistencies,
                "coherence_score": coherence_eval.coherence_score,
                "risk_score": risk_eval.risk_score,
            },
        ))

    # Emit coherence evaluated event
    event_log.append(Event(
        event_type=EventType.COHERENCE_EVALUATED,
        trace_id=trace_id,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload={
            "coherence_score": coherence_eval.coherence_score,
            "verdict": coherence_eval.verdict,
            "harness_version": coherence_eval.harness_version,
            "results_count": len(coherence_eval.results),
            "inconsistencies": coherence_eval.inconsistencies,
        },
    ))

    # Auto-create review if warn or inconsistencies
    if coherence_eval.verdict == "warn" or coherence_eval.inconsistencies:
        try:
            reviews.request_review(intent.id, trigger="coherence")
        except Exception:
            log.warning("Failed to auto-request review for intent %s: %s", intent.id, exc_info=True)

    if coherence_eval.verdict == "fail":
        return None, block_intent(
            intent,
            f"Coherence score {coherence_eval.coherence_score:.1f} below threshold",
            risk_eval=risk_eval,
            trace_id=trace_id,
        )

    return coherence_eval, None


# ---------------------------------------------------------------------------
# Step 4: Evaluate policy gates
# ---------------------------------------------------------------------------

def _evaluate_policy_step(
    intent: Intent,
    checks_passed: list[str],
    risk_eval: RiskEval,
    cfg: policy.PolicyConfig,
    sim: Simulation,
    trace_id: str,
    coherence_eval: CoherenceEvaluation | None = None,
) -> tuple[PolicyEvaluation | None, dict[str, Any] | None]:
    """Step 4: Evaluate policy gates (verification, containment, entropy, security, coherence)."""
    log.info("step 4: evaluate policy", extra={"intent_id": intent.id, "step": "policy"})
    coherence_score = coherence_eval.coherence_score if coherence_eval else None
    policy_eval = policy.evaluate(
        risk_level=intent.risk_level,
        checks_passed=checks_passed,
        entropy_delta=risk_eval.entropy_score,
        containment_score=risk_eval.containment_score,
        coherence_score=coherence_score,
        config=cfg,
        origin_type=intent.origin_type,
    )

    event_log.append(Event(
        event_type=EventType.POLICY_EVALUATED,
        trace_id=trace_id,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=PolicyPayload(
            verdict=policy_eval.verdict.value,
            gates=[GatePayload(gate=g.gate.value, passed=g.passed, reason=g.reason,
                               value=g.value, threshold=g.threshold) for g in policy_eval.gates],
            profile_used=policy_eval.profile_used,
            trace_id=trace_id,
        ).to_dict(),
        evidence={"verdict": policy_eval.verdict.value, "trace_id": trace_id},
    ))

    if policy_eval.verdict == PolicyVerdict.BLOCK:
        blocked_gates = [g.gate.value for g in policy_eval.gates if not g.passed]
        return None, block_intent(intent,
                            f"Policy blocked: gates {blocked_gates}",
                            sim=sim, risk_eval=risk_eval, policy_eval=policy_eval,
                            trace_id=trace_id)
    return policy_eval, None


# ---------------------------------------------------------------------------
# Step 5: Risk gate (shadow/enforce with gradual rollout)
# ---------------------------------------------------------------------------

def _evaluate_risk_gate_step(
    intent: Intent,
    risk_eval: RiskEval,
    policy_eval: PolicyEvaluation,
    sim: Simulation,
    trace_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Step 5: Risk gate (shadow/enforce with gradual rollout)."""
    log.info("step 5: risk gate", extra={"intent_id": intent.id, "step": "risk_gate"})
    risk_gate = policy.evaluate_risk_gate(
        risk_score=risk_eval.risk_score,
        damage_score=risk_eval.damage_score,
        propagation_score=risk_eval.propagation_score,
        intent_id=intent.id,
    )

    if risk_gate["enforced"]:
        return None, block_intent(intent,
                            f"Risk gate enforced: {risk_gate['breaches']}",
                            sim=sim, risk_eval=risk_eval, policy_eval=policy_eval,
                            trace_id=trace_id)
    return risk_gate, None


# ---------------------------------------------------------------------------
# Step 6: Finalize validation
# ---------------------------------------------------------------------------

def _finalize_validation(
    intent: Intent,
    sim: Simulation,
    risk_eval: RiskEval,
    policy_eval: PolicyEvaluation,
    risk_gate: dict[str, Any],
    trace_id: str,
    coherence_eval: CoherenceEvaluation | None = None,
) -> dict[str, Any]:
    """Step 6: Mark VALIDATED, record event, build response."""
    log.info("step 6: finalize validation", extra={"intent_id": intent.id, "step": "finalize"})
    event_log.update_intent_status(intent.id, Status.VALIDATED)
    event_log.append(Event(
        event_type=EventType.INTENT_VALIDATED,
        trace_id=trace_id,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload={"source": intent.source, "target": intent.target, "trace_id": trace_id},
        evidence={"risk_score": risk_eval.risk_score, "policy_verdict": "ALLOW", "trace_id": trace_id},
    ))

    result: dict[str, Any] = {
        "decision": "validated",
        "intent_id": intent.id,
        "trace_id": trace_id,
        "simulation": {"mergeable": sim.mergeable, "files_changed": sim.files_changed},
        "risk": risk_eval.to_dict(),
        "policy": {"verdict": "ALLOW", "gates": [{"gate": g.gate.value, "passed": g.passed} for g in policy_eval.gates]},
        "risk_gate": risk_gate,
    }
    if coherence_eval:
        result["coherence"] = coherence_eval.to_dict()
    return result


# ---------------------------------------------------------------------------
# Public helper: block an intent
# ---------------------------------------------------------------------------

def block_intent(
    intent: Intent,
    reason: str,
    sim: Simulation | None = None,
    risk_eval: RiskEval | None = None,
    policy_eval: PolicyEvaluation | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    event_log.append(Event(
        event_type=EventType.INTENT_BLOCKED,
        trace_id=trace_id or "",
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=BlockPayload(reason=reason, trace_id=trace_id or "").to_dict(),
        evidence={"reason": reason, "trace_id": trace_id},
    ))
    result: dict[str, Any] = {"decision": "blocked", "intent_id": intent.id, "reason": reason}
    if trace_id:
        result["trace_id"] = trace_id
    if sim:
        result["simulation"] = {"mergeable": sim.mergeable, "conflicts": sim.conflicts}
    if risk_eval:
        result["risk"] = risk_eval.to_dict()
    if policy_eval:
        result["policy"] = {"verdict": "BLOCK",
                            "gates": [{"gate": g.gate.value, "passed": g.passed, "reason": g.reason}
                                      for g in policy_eval.gates]}
    return result
