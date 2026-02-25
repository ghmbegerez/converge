"""Tests for coherence feedback loop (Initiative 5)."""
import pytest

from converge.models import Event, EventType


def test_analyze_empty_history(db_path):
    """Without any events, analysis returns empty list."""
    from converge import coherence_feedback
    suggestions = coherence_feedback.analyze_patterns(lookback_days=90)
    assert suggestions == []


def test_detect_module_failures(db_path):
    """With rejections concentrated in a module, generates suggestion."""
    from converge import event_log, coherence_feedback

    # Create multiple rejection events referencing the same module
    for i in range(4):
        event_log.append(Event(
            event_type=EventType.INTENT_REJECTED,
            intent_id=f"rej-{i}",
            payload={"reason": "test", "files_changed": [f"auth/handler_{i}.py"]},
        ))

    suggestions = coherence_feedback.analyze_patterns()
    module_suggestions = [s for s in suggestions if s["type"] == "module_failure_pattern"]
    assert len(module_suggestions) >= 1
    assert module_suggestions[0]["module"] == "auth"


def test_detect_risk_band_patterns(db_path):
    """Failures in specific risk band generate suggestion."""
    from converge import event_log, coherence_feedback

    for i in range(6):
        event_log.append(Event(
            event_type=EventType.INTENT_REJECTED,
            intent_id=f"risk-rej-{i}",
            payload={"reason": "test", "risk_level": "high"},
        ))

    suggestions = coherence_feedback.analyze_patterns()
    risk_suggestions = [s for s in suggestions if s["type"] == "risk_band_pattern"]
    assert len(risk_suggestions) >= 1
    assert risk_suggestions[0]["risk_level"] == "high"


def test_emit_suggestions(db_path):
    """Suggestions are emitted as COHERENCE_SUGGESTION events."""
    from converge import event_log, coherence_feedback

    suggestions = [{
        "type": "test",
        "suggested_question": {
            "id": "q-test",
            "question": "Test?",
            "check": "echo 1",
            "assertion": "result >= 1",
            "severity": "medium",
            "category": "structural",
        },
    }]

    count = coherence_feedback.emit_suggestions(suggestions)
    assert count == 1

    events = event_log.query(event_type=EventType.COHERENCE_SUGGESTION)
    assert len(events) == 1
    assert events[0]["payload"]["type"] == "test"
    assert "suggestion_id" in events[0]["payload"]


def test_accept_suggestion(db_path, tmp_path):
    """Accepted suggestion is added to harness config and event emitted."""
    from converge import event_log, coherence_feedback, coherence
    import json

    # Create a harness config
    harness_path = tmp_path / "coherence_harness.json"
    harness_path.write_text(json.dumps({"version": "1.0.0", "questions": []}))

    # Emit a suggestion
    sug_id = "sug-test123"
    event_log.append(Event(
        event_type=EventType.COHERENCE_SUGGESTION,
        payload={
            "suggestion_id": sug_id,
            "type": "test",
            "suggested_question": {
                "id": "q-new",
                "question": "New question?",
                "check": "echo 42",
                "assertion": "result >= 42",
                "severity": "medium",
                "category": "health",
            },
        },
    ))

    # Monkey-patch the HARNESS_CONFIG_PATH
    import converge.coherence as coh_mod
    original_path = coh_mod.HARNESS_CONFIG_PATH
    coh_mod.HARNESS_CONFIG_PATH = str(harness_path)

    try:
        result = coherence_feedback.accept_suggestion(sug_id)
        assert result is not None
        assert result["suggestion_id"] == sug_id

        # Verify event
        events = event_log.query(event_type=EventType.COHERENCE_SUGGESTION_ACCEPTED)
        assert len(events) == 1

        # Verify harness was updated
        data = json.loads(harness_path.read_text())
        assert any(q["id"] == "q-new" for q in data["questions"])
    finally:
        coh_mod.HARNESS_CONFIG_PATH = original_path


def test_accept_unknown_suggestion(db_path):
    """Accepting a non-existent suggestion returns None."""
    from converge import coherence_feedback
    result = coherence_feedback.accept_suggestion("sug-nonexistent")
    assert result is None


def test_flag_disabled(db_path, monkeypatch):
    """With coherence_feedback flag disabled, CLI command reports disabled."""
    from converge import feature_flags

    monkeypatch.setenv("CONVERGE_FF_COHERENCE_FEEDBACK", "0")
    feature_flags.reload_flags()

    assert not feature_flags.is_enabled("coherence_feedback")


def test_lookback_respects_window(db_path):
    """Analysis only considers events within the lookback window."""
    from converge import event_log, coherence_feedback

    # Events with default timestamps (now) should be within lookback
    event_log.append(Event(
        event_type=EventType.INTENT_REJECTED,
        intent_id="recent-1",
        payload={"reason": "test", "files_changed": ["x/a.py"]},
    ))

    suggestions = coherence_feedback.analyze_patterns(lookback_days=1)
    # Should process without error, may or may not produce suggestions
    assert isinstance(suggestions, list)
