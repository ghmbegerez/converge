"""Core engine: the 3 invariants.

Invariant 1: mergeable(i, t) = can_merge(M(t), Δi) ∧ checks_pass
Invariant 2: If M(t) advances → revalidate
Invariant 3: retries > N → reject

This module is the hot path. Stateless per decision. Every call produces
one or more Events that get appended to the event log.

The validation pipeline (Invariant 1) has been extracted to
``converge.validation_pipeline``.  ``validate_intent()`` delegates to
``run_validation_pipeline()`` so the public API is unchanged.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from converge.models import (
    CheckResult,
    Event,
    EventType,
    Intent,
    RiskLevel,
    Simulation,
    Status,
)
from converge import event_log, policy, scm
from converge.defaults import CHECK_OUTPUT_LIMIT, CHECK_TIMEOUT_SECONDS, DEFAULT_TARGET_BRANCH
from converge.event_payloads import (
    CheckPayload,
    MergeFailedPayload,
    MergePayload,
    RejectPayload,
    SimulationPayload,
)
from converge.validation_pipeline import run_validation_pipeline, block_intent


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
# Delegates to converge.validation_pipeline.run_validation_pipeline()
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

    Implementation lives in ``converge.validation_pipeline``.
    """
    log.info(
        "validate_intent start",
        extra={"intent_id": intent.id,
               "skip_checks": skip_checks, "use_last_simulation": use_last_simulation},
    )
    result = run_validation_pipeline(
        intent,
        sim=sim,
        use_last_simulation=use_last_simulation,
        skip_checks=skip_checks,
        config=config,
        cwd=cwd,
    )
    log.info(
        "validate_intent done: %s",
        result.get("decision", "unknown"),
        extra={"intent_id": intent.id, "decision": result.get("decision")},
    )
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
        log.info("process_queue skipped: lock held")
        return [{"error": "Queue lock held. Another process may be running.", "lock": lock_info}]

    log.info("process_queue lock acquired")
    try:
        results = []
        intents = event_log.list_intents(status=Status.VALIDATED.value, limit=limit)
        log.info("process_queue found %d validated intents", len(intents))

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
    log.info(
        "processing intent %s (retries=%d, status=%s)",
        intent.id, intent.retries, intent.status.value,
        extra={"intent_id": intent.id, "step": "process_single"},
    )
    # Invariant 3: bounded retry
    if intent.retries >= opts.max_retries:
        return _reject_max_retries(intent, opts.max_retries)

    # Check for pending reviews before processing
    pending_reviews = event_log.list_review_tasks(
        intent_id=intent.id, status="pending",
    )
    assigned_reviews = event_log.list_review_tasks(
        intent_id=intent.id, status="assigned",
    )
    if pending_reviews or assigned_reviews:
        review_count = len(pending_reviews) + len(assigned_reviews)
        return {
            "decision": "review_pending",
            "intent_id": intent.id,
            "pending_reviews": review_count,
            "reason": f"{review_count} review(s) still pending",
        }

    # Check for rejected reviews → block the intent
    completed_reviews = event_log.list_review_tasks(
        intent_id=intent.id, status="completed",
    )
    rejected = [t for t in completed_reviews if t.resolution == "rejected"]
    if rejected:
        return block_intent(intent, "Review rejected", trace_id=event_log.fresh_trace_id())

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
    log.info(
        "executing merge %s -> %s",
        intent.source, intent.target,
        extra={"intent_id": intent.id, "step": "merge"},
    )
    try:
        sha = scm.execute_merge_safe(intent.source, intent.target, cwd=cwd)
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
