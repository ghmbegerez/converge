"""Coherence harness: systemic coherence evaluation via configurable questions.

Evaluates whether a change maintains system coherence by running shell-based
checks against configurable assertions and baselines. Each question produces
a measurable result that is compared against a baseline or assertion.

Scoring: each question contributes points by severity (critical=30, high=20,
medium=10). Score = 100 - points_lost, clamped to [0, 100].
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from converge import event_log
from converge.defaults import COHERENCE_PASS_THRESHOLD, COHERENCE_WARN_THRESHOLD
from converge.models import (
    CoherenceEvaluation,
    CoherenceQuestion,
    CoherenceResult,
    CoherenceVerdict,
    Event,
    EventType,
    RiskEval,
)

HARNESS_CONFIG_PATH = ".converge/coherence_harness.json"
QUESTION_TIMEOUT_SECONDS = 60

SEVERITY_WEIGHTS: dict[str, int] = {
    "critical": 30,
    "high": 20,
    "medium": 10,
}

HARNESS_TEMPLATE: dict[str, Any] = {
    "version": "1.1.0",
    "questions": [
        {
            "id": "q-test-count",
            "question": "Has the test file count decreased?",
            "check": "find tests/ -name 'test_*.py' | wc -l",
            "assertion": "result >= baseline",
            "severity": "high",
            "category": "structural",
            "enabled": True,
        },
        {
            "id": "q-no-fixme-growth",
            "question": "Has the TODO/FIXME count increased?",
            "check": "grep -r 'TODO\\|FIXME' src/ --include='*.py' | wc -l",
            "assertion": "result <= baseline",
            "severity": "medium",
            "category": "structural",
            "enabled": True,
        },
        {
            "id": "q-no-large-files",
            "question": "Were files larger than 1MB added to source?",
            "check": "find src/ -type f -size +1M | wc -l",
            "assertion": "result == 0",
            "severity": "high",
            "category": "structural",
            "enabled": True,
        },
        {
            "id": "q-src-file-count",
            "question": "Is the source file count stable?",
            "check": "find src/ -name '*.py' | wc -l",
            "assertion": "result >= baseline",
            "severity": "medium",
            "category": "structural",
            "enabled": False,
        },
        {
            "id": "q-test-ratio",
            "question": "Is the test-to-source ratio adequate?",
            "check": "echo $(( $(find tests/ -name 'test_*.py' | wc -l) * 100 / $(find src/ -name '*.py' | wc -l) ))",
            "assertion": "result >= baseline",
            "severity": "medium",
            "category": "structural",
            "enabled": False,
        },
    ],
}


# ---------------------------------------------------------------------------
# Load questions from config
# ---------------------------------------------------------------------------

def load_questions(path: str | Path | None = None) -> list[CoherenceQuestion]:
    """Load coherence questions from the harness config file."""
    config_path = Path(path) if path else Path(HARNESS_CONFIG_PATH)
    if not config_path.exists():
        return []

    with open(config_path) as f:
        data = json.load(f)

    questions = []
    for q in data.get("questions", []):
        # Filter by enabled field (defaults to True for backward compatibility)
        if not q.get("enabled", True):
            continue
        questions.append(CoherenceQuestion(
            id=q["id"],
            question=q["question"],
            check=q["check"],
            assertion=q["assertion"],
            severity=q.get("severity", "high"),
            category=q.get("category", "structural"),
        ))
    return questions


def load_harness_version(path: str | Path | None = None) -> str:
    """Load the harness config version string."""
    config_path = Path(path) if path else Path(HARNESS_CONFIG_PATH)
    if not config_path.exists():
        return "none"
    with open(config_path) as f:
        data = json.load(f)
    return data.get("version", "unknown")


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def load_baselines() -> dict[str, float]:
    """Load baselines from the most recent COHERENCE_BASELINE_UPDATED events."""
    events = event_log.query(
        event_type=EventType.COHERENCE_BASELINE_UPDATED,
        limit=1,
    )
    if not events:
        return {}
    payload = events[0].get("payload", {}) if isinstance(events[0], dict) else events[0].payload
    return payload.get("baselines", {})


def update_baselines(results: list[CoherenceResult]) -> dict[str, float]:
    """Emit COHERENCE_BASELINE_UPDATED event with current values as new baselines."""
    baselines = {r.question_id: r.value for r in results if r.error is None}
    event_log.append(Event(
        event_type=EventType.COHERENCE_BASELINE_UPDATED,
        payload={"baselines": baselines},
    ))
    return baselines


# ---------------------------------------------------------------------------
# Run a single question
# ---------------------------------------------------------------------------

def run_question(
    q: CoherenceQuestion,
    workdir: str | Path | None = None,
    baselines: dict[str, float] | None = None,
) -> CoherenceResult:
    """Execute a single coherence question and evaluate its assertion."""
    cwd = str(workdir) if workdir else None
    baselines = baselines or {}

    # Run the check command
    try:
        proc = subprocess.run(
            q.check,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=QUESTION_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            return CoherenceResult(
                question_id=q.id,
                question=q.question,
                verdict="fail",
                value=0.0,
                baseline=baselines.get(q.id),
                assertion=q.assertion,
                error=f"Command failed (rc={proc.returncode}): {proc.stderr[:200]}",
            )
        # Parse numeric result from stdout
        raw = proc.stdout.strip()
        value = _parse_numeric(raw)
    except subprocess.TimeoutExpired:
        return CoherenceResult(
            question_id=q.id,
            question=q.question,
            verdict="fail",
            value=0.0,
            baseline=baselines.get(q.id),
            assertion=q.assertion,
            error="Command timed out",
        )
    except Exception as e:
        return CoherenceResult(
            question_id=q.id,
            question=q.question,
            verdict="fail",
            value=0.0,
            baseline=baselines.get(q.id),
            assertion=q.assertion,
            error=str(e),
        )

    # Evaluate assertion
    baseline = baselines.get(q.id)
    passed = _evaluate_assertion(q.assertion, value, baseline)

    return CoherenceResult(
        question_id=q.id,
        question=q.question,
        verdict="pass" if passed else "fail",
        value=value,
        baseline=baseline,
        assertion=q.assertion,
    )


# ---------------------------------------------------------------------------
# Evaluate all questions
# ---------------------------------------------------------------------------

def evaluate(
    questions: list[CoherenceQuestion],
    workdir: str | Path | None = None,
    baselines: dict[str, float] | None = None,
    pass_threshold: int = COHERENCE_PASS_THRESHOLD,
    warn_threshold: int = COHERENCE_WARN_THRESHOLD,
) -> CoherenceEvaluation:
    """Run all coherence questions and compute aggregate score.

    Scoring: each failed question loses points by severity weight.
    Score = 100 - total_penalty, clamped to [0, 100].
    """
    if not questions:
        return CoherenceEvaluation(
            coherence_score=100.0,
            verdict=CoherenceVerdict.PASS.value,
            results=[],
            harness_version="none",
        )

    if baselines is None:
        baselines = load_baselines()

    harness_version = load_harness_version()
    results: list[CoherenceResult] = []

    for q in questions:
        result = run_question(q, workdir=workdir, baselines=baselines)
        results.append(result)

    # Calculate score
    penalty = 0
    for r in results:
        if r.verdict != "pass":
            weight = SEVERITY_WEIGHTS.get(
                _question_severity(r.question_id, questions), 20,
            )
            penalty += weight

    score = max(0.0, min(100.0, 100.0 - penalty))

    # Determine verdict
    if score >= pass_threshold:
        verdict = CoherenceVerdict.PASS.value
    elif score >= warn_threshold:
        verdict = CoherenceVerdict.WARN.value
    else:
        verdict = CoherenceVerdict.FAIL.value

    return CoherenceEvaluation(
        coherence_score=score,
        verdict=verdict,
        results=results,
        harness_version=harness_version,
    )


# ---------------------------------------------------------------------------
# Consistency cross-validation (Fase 3)
# ---------------------------------------------------------------------------

def check_consistency(
    coherence_eval: CoherenceEvaluation,
    risk_eval: RiskEval,
) -> list[dict[str, Any]]:
    """Cross-validate coherence declarations vs objective risk metrics."""
    inconsistencies: list[dict[str, Any]] = []

    # If coherence passed but risk score is elevated
    if coherence_eval.coherence_score > 75 and risk_eval.risk_score > 50:
        inconsistencies.append({
            "type": "score_mismatch",
            "coherence_score": coherence_eval.coherence_score,
            "risk_score": risk_eval.risk_score,
            "message": "Coherence harness passed but risk is elevated",
        })

    # If all questions passed but structural bombs detected
    if (all(r.verdict == "pass" for r in coherence_eval.results)
            and coherence_eval.results
            and risk_eval.bombs):
        inconsistencies.append({
            "type": "bomb_undetected",
            "bombs": [b.get("type", "unknown") for b in risk_eval.bombs],
            "message": "Structural degradation detected but coherence harness didn't flag it",
        })

    # If propagation is high but no scope questions in harness
    scope_qs = [r for r in coherence_eval.results if r.question_id.startswith("q-scope")]
    if risk_eval.propagation_score > 40 and not scope_qs:
        inconsistencies.append({
            "type": "missing_scope_validation",
            "propagation_score": risk_eval.propagation_score,
            "message": "High propagation but no scope questions in harness",
        })

    return inconsistencies


# ---------------------------------------------------------------------------
# Init template
# ---------------------------------------------------------------------------

def init_harness(path: str | Path | None = None) -> dict[str, Any]:
    """Create the coherence harness config file with a default template."""
    config_path = Path(path) if path else Path(HARNESS_CONFIG_PATH)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        return {"status": "exists", "path": str(config_path)}

    with open(config_path, "w") as f:
        json.dump(HARNESS_TEMPLATE, f, indent=2)
        f.write("\n")

    return {"status": "created", "path": str(config_path), "questions": len(HARNESS_TEMPLATE["questions"])}


def list_questions(path: str | Path | None = None) -> dict[str, Any]:
    """List all configured coherence questions with current baselines."""
    questions = load_questions(path)
    baselines = load_baselines()
    version = load_harness_version(path)

    return {
        "version": version,
        "questions": [
            {
                "id": q.id,
                "question": q.question,
                "check": q.check,
                "assertion": q.assertion,
                "severity": q.severity,
                "category": q.category,
                "baseline": baselines.get(q.id),
            }
            for q in questions
        ],
        "baselines": baselines,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_numeric(raw: str) -> float:
    """Parse a numeric value from command output."""
    cleaned = raw.strip()
    if not cleaned:
        return 0.0
    # Take the last line if multiline
    lines = cleaned.splitlines()
    last = lines[-1].strip()
    return float(last)


def _evaluate_assertion(assertion: str, result: float, baseline: float | None) -> bool:
    """Evaluate a simple assertion string.

    Supported forms:
      - "result >= baseline"
      - "result <= baseline"
      - "result > baseline"
      - "result < baseline"
      - "result == 0"
      - "result == <number>"
      - "result >= <number>"
      - "result <= <number>"
    """
    assertion = assertion.strip()

    # Replace "result" and "baseline" with actual values
    # If assertion references baseline but none exists, pass (no baseline yet)
    if "baseline" in assertion and baseline is None:
        return True

    try:
        # Build safe evaluation context
        ctx: dict[str, Any] = {"result": result}
        if baseline is not None:
            ctx["baseline"] = baseline

        # Simple parsing: split into (left, op, right)
        return _safe_eval_assertion(assertion, ctx)
    except (ValueError, TypeError):
        return False


def _safe_eval_assertion(assertion: str, ctx: dict[str, float | None]) -> bool:
    """Safely evaluate a comparison assertion without exec/eval.

    Supports compound assertions joined by ``AND`` or ``OR`` (case-insensitive).
    Each clause is a simple comparison: ``<token> <op> <token>``.

    Examples::

        result >= baseline
        result >= 0 AND result <= 100
        result == 0 OR baseline == 0
    """
    upper = assertion.upper()
    if " OR " in upper:
        parts = _split_compound(assertion, " OR ")
        return any(_eval_single_comparison(p.strip(), ctx) for p in parts)
    if " AND " in upper:
        parts = _split_compound(assertion, " AND ")
        return all(_eval_single_comparison(p.strip(), ctx) for p in parts)
    return _eval_single_comparison(assertion, ctx)


def _split_compound(assertion: str, sep: str) -> list[str]:
    """Split assertion by separator, case-insensitive."""
    import re
    return re.split(re.escape(sep), assertion, flags=re.IGNORECASE)


def _eval_single_comparison(assertion: str, ctx: dict[str, float | None]) -> bool:
    """Evaluate a single comparison clause."""
    ops = [">=", "<=", "==", "!=", ">", "<"]
    for op in ops:
        if op in assertion:
            parts = assertion.split(op, 1)
            if len(parts) == 2:
                left = _resolve_value(parts[0].strip(), ctx)
                right = _resolve_value(parts[1].strip(), ctx)
                if left is None or right is None:
                    return True  # can't evaluate, assume pass
                if op == ">=":
                    return left >= right
                if op == "<=":
                    return left <= right
                if op == "==":
                    return left == right
                if op == "!=":
                    return left != right
                if op == ">":
                    return left > right
                if op == "<":
                    return left < right
    return False


def _resolve_value(token: str, ctx: dict[str, Any]) -> float | None:
    """Resolve a token to its float value."""
    if token in ctx:
        return ctx[token]
    try:
        return float(token)
    except (ValueError, TypeError):
        return None


def _question_severity(question_id: str, questions: list[CoherenceQuestion]) -> str:
    """Look up the severity of a question by its ID."""
    for q in questions:
        if q.id == question_id:
            return q.severity
    return "high"
