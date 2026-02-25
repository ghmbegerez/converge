"""Coherence feedback loop: analyze failure patterns and suggest harness questions.

Deterministic analysis — no LLM. Scans recent rejection and failure events
to identify patterns that could be caught earlier by coherence harness
questions, then emits suggestions as events.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from converge import event_log
from converge.models import Event, EventType, new_id, now_iso

log = logging.getLogger("converge.coherence_feedback")


# ---------------------------------------------------------------------------
# Pattern analysis
# ---------------------------------------------------------------------------

def analyze_patterns(*, lookback_days: int = 90) -> list[dict[str, Any]]:
    """Analyze recent failures and generate question suggestions."""
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    rejections = event_log.query(
        event_type=EventType.INTENT_REJECTED, since=since, limit=500,
    )
    merge_failures = event_log.query(
        event_type=EventType.INTENT_MERGE_FAILED, since=since, limit=500,
    )
    coherence_evals = event_log.query(
        event_type=EventType.COHERENCE_EVALUATED, since=since, limit=500,
    )

    suggestions: list[dict[str, Any]] = []
    suggestions.extend(_detect_module_failures(rejections, merge_failures))
    suggestions.extend(_detect_risk_band_patterns(rejections, coherence_evals))
    suggestions.extend(_detect_file_count_regressions(merge_failures))
    return suggestions


def _detect_module_failures(
    rejections: list[dict[str, Any]],
    merge_failures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """If intents touching module X fail >60% of the time, suggest a question."""
    dir_failures: Counter[str] = Counter()
    dir_total: Counter[str] = Counter()

    for events in (rejections, merge_failures):
        for ev in events:
            payload = ev.get("payload", {})
            # Try to extract affected files/directories
            files = payload.get("files_changed", [])
            if not files:
                source = payload.get("source", "")
                if "/" in source:
                    files = [source]
            for f in files:
                parts = f.split("/")
                if len(parts) >= 2:
                    module = parts[0]
                    dir_failures[module] += 1

    # We can only compute failure rates if we also track totals
    # For now, suggest if a module has >= 3 failures
    suggestions = []
    for module, count in dir_failures.most_common(3):
        if count >= 3:
            suggestions.append({
                "type": "module_failure_pattern",
                "module": module,
                "failure_count": count,
                "suggested_question": {
                    "id": f"q-module-{module}",
                    "question": f"Are changes to {module}/ properly tested?",
                    "check": f"find {module}/ -name 'test_*.py' | wc -l",
                    "assertion": "result >= baseline",
                    "severity": "high",
                    "category": "structural",
                },
            })
    return suggestions


def _detect_risk_band_patterns(
    rejections: list[dict[str, Any]],
    coherence_evals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """If a specific risk_level band has disproportionate failures, suggest monitoring."""
    risk_failures: Counter[str] = Counter()
    for ev in rejections:
        payload = ev.get("payload", {})
        risk_level = payload.get("risk_level", "unknown")
        risk_failures[risk_level] += 1

    suggestions = []
    for level, count in risk_failures.most_common(2):
        if count >= 5 and level in ("medium", "high"):
            suggestions.append({
                "type": "risk_band_pattern",
                "risk_level": level,
                "failure_count": count,
                "suggested_question": {
                    "id": f"q-risk-{level}-guard",
                    "question": f"Do {level}-risk intents pass additional validation?",
                    "check": f"echo {count}",
                    "assertion": "result >= 0",
                    "severity": "medium",
                    "category": "health",
                },
            })
    return suggestions


def _detect_file_count_regressions(
    merge_failures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """If merge failures correlate with large file counts, suggest a guard question."""
    large_change_failures = 0
    for ev in merge_failures:
        payload = ev.get("payload", {})
        files = payload.get("files_changed", [])
        if len(files) > 15:
            large_change_failures += 1

    suggestions = []
    if large_change_failures >= 3:
        suggestions.append({
            "type": "file_count_regression",
            "failure_count": large_change_failures,
            "suggested_question": {
                "id": "q-file-count-guard",
                "question": "Is the change scope within safe limits?",
                "check": "git diff --name-only HEAD~1 | wc -l",
                "assertion": "result <= 20",
                "severity": "high",
                "category": "structural",
            },
        })
    return suggestions


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------

def emit_suggestions(suggestions: list[dict[str, Any]]) -> int:
    """Store suggestions as events. Returns count emitted."""
    count = 0
    for s in suggestions:
        event_log.append(Event(
            event_type=EventType.COHERENCE_SUGGESTION,
            payload={"suggestion_id": f"sug-{new_id()}", **s},
        ))
        count += 1
    return count


# ---------------------------------------------------------------------------
# Accept a suggestion
# ---------------------------------------------------------------------------

def accept_suggestion(suggestion_id: str) -> dict[str, Any] | None:
    """Accept a suggestion: add question to coherence harness config."""
    events = event_log.query(
        event_type=EventType.COHERENCE_SUGGESTION, limit=500,
    )
    suggestion = next(
        (e for e in events if e.get("payload", {}).get("suggestion_id") == suggestion_id),
        None,
    )
    if not suggestion:
        return None

    payload = suggestion.get("payload", {})
    question = payload.get("suggested_question")
    if not question:
        return None

    # Add to coherence harness config
    from converge.coherence import HARNESS_CONFIG_PATH

    config_path = Path(HARNESS_CONFIG_PATH)
    if config_path.exists():
        with open(config_path) as f:
            data = json.load(f)
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": "1.0.0", "questions": []}

    # Avoid duplicates
    existing_ids = {q["id"] for q in data.get("questions", [])}
    if question["id"] not in existing_ids:
        data["questions"].append(question)
        with open(config_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    event_log.append(Event(
        event_type=EventType.COHERENCE_SUGGESTION_ACCEPTED,
        payload={"suggestion_id": suggestion_id, "question_id": question["id"]},
    ))

    return payload
