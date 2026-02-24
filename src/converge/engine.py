"""Core engine: the 3 invariants.

Invariant 1: mergeable(i, t) = can_merge(M(t), Δi) ∧ checks_pass
Invariant 2: If M(t) advances → revalidate
Invariant 3: retries > N → reject

This module is the hot path. Stateless per decision. Every call produces
one or more Events that get appended to the event log.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from converge.models import (
    CheckResult,
    Event,
    EventType,
    Intent,
    PolicyEvaluation,
    PolicyVerdict,
    RiskEval,
    RiskLevel,
    Simulation,
    Status,
)
from converge import analytics, event_log, policy, risk, scm
from converge.defaults import CHECK_OUTPUT_LIMIT, CHECK_TIMEOUT_SECONDS, CONFLICT_DISPLAY_LIMIT, DEFAULT_TARGET_BRANCH
from converge.event_payloads import (
    BlockPayload,
    CheckPayload,
    GatePayload,
    MergeFailedPayload,
    MergePayload,
    PolicyPayload,
    RejectPayload,
    SimulationPayload,
)


# ---------------------------------------------------------------------------
# Simulate (Invariant 1, part 1: can_merge)
# ---------------------------------------------------------------------------

def simulate(
    source: str,
    target: str,
    intent_id: str | None = None,
    tenant_id: str | None = None,
    cwd: str | Path | None = None,
    trace_id: str | None = None,
) -> Simulation:
    """Run merge simulation and record event."""
    sim = scm.simulate_merge(source, target, cwd=cwd)
    event_log.append(Event(
        event_type=EventType.SIMULATION_COMPLETED,
        trace_id=trace_id or "",
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload=SimulationPayload(
            mergeable=sim.mergeable,
            conflicts=sim.conflicts,
            files_changed=sim.files_changed,
            source=source,
            target=target,
        ).to_dict(),
        evidence={"source": source, "target": target, "conflict_count": len(sim.conflicts)},
    ))
    return sim


def simulate_from_last(
    intent_id: str,
) -> Simulation | None:
    """Retrieve last simulation from event log (dev fallback)."""
    events = event_log.query(event_type=EventType.SIMULATION_COMPLETED, intent_id=intent_id, limit=1)
    if not events:
        return None
    p = events[0]["payload"]
    return Simulation(
        mergeable=p["mergeable"],
        conflicts=p.get("conflicts", []),
        files_changed=p.get("files_changed", []),
        source=p.get("source", ""),
        target=p.get("target", ""),
    )


# ---------------------------------------------------------------------------
# Checks (Invariant 1, part 2: checks_pass)
# ---------------------------------------------------------------------------

SUPPORTED_CHECKS = {"lint", "unit_tests", "integration_tests", "security_scan", "contract_tests"}


def run_checks(
    checks: list[str],
    intent_id: str | None = None,
    tenant_id: str | None = None,
    cwd: str | Path | None = None,
    trace_id: str | None = None,
) -> list[CheckResult]:
    """Run requested checks as subprocesses. Record events for each."""
    results = []
    check_commands = {
        "lint": ["make", "lint"],
        "unit_tests": ["make", "test"],
        "integration_tests": ["make", "test-integration"],
        "security_scan": ["make", "security-scan"],
        "contract_tests": ["make", "test-contract"],
    }

    for check_type in checks:
        if check_type not in SUPPORTED_CHECKS:
            continue
        cmd = check_commands.get(check_type, ["echo", "no-op"])
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=CHECK_TIMEOUT_SECONDS)
            passed = r.returncode == 0
            details = r.stdout[:CHECK_OUTPUT_LIMIT] if passed else r.stderr[:CHECK_OUTPUT_LIMIT]
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            passed = False
            details = str(e)

        result = CheckResult(check_type=check_type, passed=passed, details=details)
        results.append(result)

        event_log.append(Event(
            event_type=EventType.CHECK_COMPLETED,
            trace_id=trace_id or "",
            intent_id=intent_id,
            tenant_id=tenant_id,
            payload=CheckPayload(check_type=check_type, passed=passed, details=details).to_dict(),
            evidence={"check_type": check_type, "passed": passed},
        ))

    return results


def checks_for_risk_level(risk_level: RiskLevel, config: policy.PolicyConfig | None = None) -> list[str]:
    """Determine which checks are required for a given risk level."""
    cfg = config or policy.load_config()
    profile = cfg.profile_for(risk_level)
    return profile.get("checks", ["lint"])


# ---------------------------------------------------------------------------
# Validate (combines simulation + checks + policy + risk → decision)
# ---------------------------------------------------------------------------

def validate_intent(
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
      mergeable(i, t) = can_merge(M(t), Δi) ∧ checks_pass
    """
    cfg = config or policy.load_config()
    trace_id = event_log.fresh_trace_id()

    sim, blocked = _resolve_simulation(intent, sim, use_last_simulation, cwd, trace_id)
    if blocked:
        return blocked

    checks_passed, blocked = _run_validation_checks(intent, cfg, skip_checks, sim, trace_id, cwd=cwd)
    if blocked:
        return blocked

    risk_eval = _evaluate_risk_step(intent, sim, cwd, trace_id)

    policy_eval, blocked = _evaluate_policy_step(intent, checks_passed, risk_eval, cfg, sim, trace_id)
    if blocked:
        return blocked

    risk_gate, blocked = _evaluate_risk_gate_step(intent, risk_eval, policy_eval, sim, trace_id)
    if blocked:
        return blocked

    return _finalize_validation(intent, sim, risk_eval, policy_eval, risk_gate, trace_id)


def _resolve_simulation(
    intent: Intent,
    sim: Simulation | None,
    use_last_simulation: bool,
    cwd: str | Path | None,
    trace_id: str,
) -> tuple[Simulation | None, dict[str, Any] | None]:
    """Step 1: Resolve or run simulation."""
    if sim is None:
        if use_last_simulation:
            sim = simulate_from_last(intent.id)
            if sim is None:
                return None, _block(intent, "No previous simulation found", trace_id=trace_id)
        else:
            sim = simulate(intent.source, intent.target,
                           intent_id=intent.id, tenant_id=intent.tenant_id, cwd=cwd,
                           trace_id=trace_id)

    if not sim.mergeable:
        return None, _block(intent, f"Merge conflicts: {', '.join(sim.conflicts[:CONFLICT_DISPLAY_LIMIT])}",
                            sim=sim, trace_id=trace_id)
    return sim, None


def _run_validation_checks(
    intent: Intent,
    cfg: policy.PolicyConfig,
    skip_checks: bool,
    sim: Simulation,
    trace_id: str,
    cwd: str | Path | None = None,
) -> tuple[list[str] | None, dict[str, Any] | None]:
    """Step 2: Execute checks."""
    if not skip_checks:
        required = checks_for_risk_level(intent.risk_level, cfg)
        results = run_checks(required, intent_id=intent.id,
                             tenant_id=intent.tenant_id, cwd=cwd,
                             trace_id=trace_id)
        checks_passed = [r.check_type for r in results if r.passed]
        failed = [r for r in results if not r.passed]
        if failed:
            names = [r.check_type for r in failed]
            return None, _block(intent, f"Checks failed: {names}",
                                sim=sim, trace_id=trace_id)
        return checks_passed, None

    return checks_for_risk_level(intent.risk_level, cfg), None


def _evaluate_risk_step(
    intent: Intent,
    sim: Simulation,
    cwd: str | Path | None,
    trace_id: str,
) -> RiskEval:
    """Step 3: Evaluate risk (never blocks — informational)."""
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
    return risk_eval


def _evaluate_policy_step(
    intent: Intent,
    checks_passed: list[str],
    risk_eval: RiskEval,
    cfg: policy.PolicyConfig,
    sim: Simulation,
    trace_id: str,
) -> tuple[PolicyEvaluation | None, dict[str, Any] | None]:
    """Step 4: Evaluate 3 policy gates."""
    policy_eval = policy.evaluate(
        risk_level=intent.risk_level,
        checks_passed=checks_passed,
        entropy_delta=risk_eval.entropy_score,
        containment_score=risk_eval.containment_score,
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
        return None, _block(intent,
                            f"Policy blocked: gates {blocked_gates}",
                            sim=sim, risk_eval=risk_eval, policy_eval=policy_eval,
                            trace_id=trace_id)
    return policy_eval, None


def _evaluate_risk_gate_step(
    intent: Intent,
    risk_eval: RiskEval,
    policy_eval: PolicyEvaluation,
    sim: Simulation,
    trace_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Step 5: Risk gate (shadow/enforce with gradual rollout)."""
    risk_gate = policy.evaluate_risk_gate(
        risk_score=risk_eval.risk_score,
        damage_score=risk_eval.damage_score,
        propagation_score=risk_eval.propagation_score,
        intent_id=intent.id,
    )

    if risk_gate["enforced"]:
        return None, _block(intent,
                            f"Risk gate enforced: {risk_gate['breaches']}",
                            sim=sim, risk_eval=risk_eval, policy_eval=policy_eval,
                            trace_id=trace_id)
    return risk_gate, None


def _finalize_validation(
    intent: Intent,
    sim: Simulation,
    risk_eval: RiskEval,
    policy_eval: PolicyEvaluation,
    risk_gate: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    """Step 6: Mark VALIDATED, record event, build response."""
    event_log.update_intent_status(intent.id, Status.VALIDATED)
    event_log.append(Event(
        event_type=EventType.INTENT_VALIDATED,
        trace_id=trace_id,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload={"source": intent.source, "target": intent.target, "trace_id": trace_id},
        evidence={"risk_score": risk_eval.risk_score, "policy_verdict": "ALLOW", "trace_id": trace_id},
    ))

    return {
        "decision": "validated",
        "intent_id": intent.id,
        "trace_id": trace_id,
        "simulation": {"mergeable": sim.mergeable, "files_changed": sim.files_changed},
        "risk": risk_eval.to_dict(),
        "policy": {"verdict": "ALLOW", "gates": [{"gate": g.gate.value, "passed": g.passed} for g in policy_eval.gates]},
        "risk_gate": risk_gate,
    }


def _block(
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


# ---------------------------------------------------------------------------
# Queue processing (Invariants 2 & 3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _QueueOpts:
    """Bundle of queue-processing options passed between internal functions."""
    max_retries: int = 3
    use_last_simulation: bool = False
    skip_checks: bool = False
    auto_confirm: bool = False
    cwd: str | Path | None = None


def _check_dependencies(
    intent: Intent,
) -> dict[str, Any] | None:
    """Return a skip-result dict if any dependency is not MERGED, else None."""
    if not intent.dependencies:
        return None
    unmet = []
    for dep_id in intent.dependencies:
        dep = event_log.get_intent(dep_id)
        if dep is None or dep.status != Status.MERGED:
            unmet.append(dep_id)
    if not unmet:
        return None
    event_log.append(Event(
        event_type=EventType.INTENT_DEPENDENCY_BLOCKED,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload={
            "reason": "Unmet dependencies",
            "unmet_dependencies": unmet,
            "all_dependencies": intent.dependencies,
            "plan_id": intent.plan_id,
        },
        evidence={"unmet_count": len(unmet), "total_deps": len(intent.dependencies)},
    ))
    return {
        "intent_id": intent.id,
        "decision": "dependency_blocked",
        "reason": "Unmet dependencies",
        "unmet_dependencies": unmet,
        "plan_id": intent.plan_id,
    }


def process_queue(
    *,
    limit: int = 20,
    target: str = DEFAULT_TARGET_BRANCH,
    auto_confirm: bool = False,
    max_retries: int = 3,
    use_last_simulation: bool = False,
    skip_checks: bool = False,
    config: policy.PolicyConfig | None = None,
    cwd: str | Path | None = None,
) -> list[dict[str, Any]]:
    """
    Process the merge queue.
    Invariant 2: revalidate against current M(t) before merging.
    Invariant 3: retries > max_retries → REJECTED.

    Uses global file lock to prevent concurrent execution.
    """
    cfg = config or policy.load_config()
    opts = _QueueOpts(max_retries=max_retries, use_last_simulation=use_last_simulation,
                      skip_checks=skip_checks, auto_confirm=auto_confirm, cwd=cwd)

    if not event_log.acquire_queue_lock():
        lock_info = event_log.get_queue_lock_info()
        return [{"error": "Queue lock held. Another process may be running.", "lock": lock_info}]

    try:
        results = []
        intents = event_log.list_intents(status=Status.VALIDATED.value, limit=limit)

        for intent in intents:
            blocked_deps = _check_dependencies(intent)
            if blocked_deps is not None:
                results.append(blocked_deps)
                continue
            result = _process_single_intent(intent, cfg, opts)
            results.append(result)

        event_log.append(Event(
            event_type=EventType.QUEUE_PROCESSED,
            payload={"processed": len(results), "limit": limit, "target": target},
            evidence={"count": len(results)},
        ))
        return results

    finally:
        event_log.release_queue_lock()


def _process_single_intent(
    intent: Intent,
    cfg: policy.PolicyConfig,
    opts: _QueueOpts,
) -> dict[str, Any]:
    """Process one intent from the queue: reject, revalidate, or merge."""
    # Invariant 3: bounded retry
    if intent.retries >= opts.max_retries:
        return _reject_max_retries(intent, opts.max_retries)

    # Invariant 2: revalidate against current M(t)
    decision = validate_intent(
        intent,
        use_last_simulation=opts.use_last_simulation,
        skip_checks=opts.skip_checks,
        config=cfg,
        cwd=opts.cwd,
    )

    if decision["decision"] == "blocked":
        return _handle_blocked_intent(intent, decision, opts.max_retries)

    # Validated → QUEUED
    event_log.update_intent_status(intent.id, Status.QUEUED)

    if opts.auto_confirm:
        _execute_merge(intent, decision, opts.cwd, max_retries=opts.max_retries)

    return decision


def _reject_max_retries(
    intent: Intent,
    max_retries: int,
) -> dict[str, Any]:
    """Reject an intent that has exceeded the retry limit."""
    event_log.update_intent_status(intent.id, Status.REJECTED, retries=intent.retries)
    event_log.append(Event(
        event_type=EventType.INTENT_REJECTED,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=RejectPayload(reason=f"Max retries ({max_retries}) exceeded", retries=intent.retries).to_dict(),
        evidence={"retries": intent.retries, "max_retries": max_retries},
    ))
    return {"intent_id": intent.id, "decision": "rejected", "reason": "max_retries_exceeded"}


def _handle_blocked_intent(
    intent: Intent,
    decision: dict[str, Any],
    max_retries: int,
) -> dict[str, Any]:
    """Increment retries on a blocked intent; reject if max reached."""
    new_retries = intent.retries + 1
    new_status = Status.REJECTED if new_retries >= max_retries else Status.READY
    event_log.update_intent_status(intent.id, new_status, retries=new_retries)

    event_type = EventType.INTENT_REJECTED if new_status == Status.REJECTED else EventType.INTENT_REQUEUED
    event_log.append(Event(
        event_type=event_type,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=RejectPayload(reason=decision["reason"], retries=new_retries).to_dict(),
        evidence={"retries": new_retries},
    ))
    decision["retries"] = new_retries
    return decision


def _execute_merge(
    intent: Intent,
    decision: dict[str, Any],
    cwd: str | Path | None,
    max_retries: int = 3,
) -> None:
    """Attempt a real merge and record the result.

    On success: set Status.MERGED and emit INTENT_MERGED.
    On failure: increment retries, set READY or REJECTED, emit INTENT_MERGE_FAILED.
    """
    try:
        sha = scm.execute_merge(intent.source, intent.target, cwd=cwd)
    except Exception as e:
        new_retries = intent.retries + 1
        new_status = Status.REJECTED if new_retries >= max_retries else Status.READY
        event_log.update_intent_status(intent.id, new_status, retries=new_retries)
        event_log.append(Event(
            event_type=EventType.INTENT_MERGE_FAILED,
            intent_id=intent.id,
            tenant_id=intent.tenant_id,
            payload=MergeFailedPayload(
                error=str(e), source=intent.source,
                target=intent.target, retries=new_retries,
            ).to_dict(),
            evidence={"error": str(e), "retries": new_retries},
        ))
        decision["decision"] = "merge_failed"
        decision["error"] = str(e)
        decision["retries"] = new_retries
        return

    event_log.update_intent_status(intent.id, Status.MERGED)
    event_log.append(Event(
        event_type=EventType.INTENT_MERGED,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=MergePayload(merged_commit=sha, source=intent.source, target=intent.target).to_dict(),
        evidence={"merged_commit": sha},
    ))
    decision["decision"] = "merged"
    decision["merged_commit"] = sha


# ---------------------------------------------------------------------------
# Post-merge confirmation
# ---------------------------------------------------------------------------

def confirm_merge(
    intent_id: str,
    merged_commit: str | None = None,
) -> dict[str, Any]:
    """Confirm a QUEUED intent as MERGED."""
    intent = event_log.get_intent(intent_id)
    if intent is None:
        return {"error": f"Intent {intent_id} not found"}
    if intent.status not in (Status.QUEUED, Status.VALIDATED):
        return {"error": f"Intent {intent_id} is {intent.status.value}, expected QUEUED or VALIDATED"}

    sha = merged_commit or f"confirmed-{intent_id[:8]}"
    event_log.update_intent_status(intent_id, Status.MERGED)
    event_log.append(Event(
        event_type=EventType.INTENT_MERGED,
        intent_id=intent_id,
        tenant_id=intent.tenant_id,
        payload=MergePayload(merged_commit=sha, source=intent.source, target=intent.target).to_dict(),
        evidence={"merged_commit": sha},
    ))
    return {"intent_id": intent_id, "status": "MERGED", "merged_commit": sha}


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------

def reset_queue(intent_id: str, set_status: str | None = None, clear_lock: bool = False) -> dict[str, Any]:
    """Reset retries for an intent and optionally change status / clear lock."""
    if clear_lock:
        event_log.force_release_queue_lock()

    intent = event_log.get_intent(intent_id)
    if intent is None:
        return {"error": f"Intent {intent_id} not found"}

    new_status = Status(set_status) if set_status else intent.status
    event_log.update_intent_status(intent_id, new_status, retries=0)
    event_log.append(Event(
        event_type=EventType.QUEUE_RESET,
        intent_id=intent_id,
        tenant_id=intent.tenant_id,
        payload={"new_status": new_status.value, "retries_reset": True},
    ))
    return {"intent_id": intent_id, "status": new_status.value, "retries": 0}


def inspect_queue(
    *,
    status: str | None = None,
    min_retries: int | None = None,
    only_actionable: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Inspect queue state with optional filters."""
    if only_actionable:
        all_intents = []
        for s in (Status.READY, Status.VALIDATED, Status.QUEUED):
            all_intents.extend(event_log.list_intents(status=s.value, limit=limit))
    elif status:
        all_intents = event_log.list_intents(status=status, limit=limit)
    else:
        all_intents = event_log.list_intents(limit=limit)

    result = []
    for intent in all_intents:
        if min_retries is not None and intent.retries < min_retries:
            continue
        result.append({
            "intent_id": intent.id,
            "status": intent.status.value,
            "retries": intent.retries,
            "priority": intent.priority,
            "source": intent.source,
            "target": intent.target,
            "risk_level": intent.risk_level.value,
        })
    return result[:limit]
