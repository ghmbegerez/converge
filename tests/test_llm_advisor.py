"""Tests for LLM review advisor (Initiative 4)."""
import os
import pytest
from unittest.mock import MagicMock, patch

from converge.llm.null_adapter import NullLLMAdapter
from converge.llm.port import ReviewAnalysis
from converge.llm import registry


@pytest.fixture(autouse=True)
def _reset_llm_registry():
    """Reset LLM registry between tests."""
    registry.reset_adapter()
    yield
    registry.reset_adapter()


def test_null_adapter_returns_empty():
    adapter = NullLLMAdapter()
    result = adapter.analyze_review({}, "", {})
    assert result.summary == ""
    assert result.confidence == 0.0
    assert result.risk_highlights == []
    assert result.suggested_focus_areas == []


def test_null_not_available():
    adapter = NullLLMAdapter()
    assert adapter.is_available() is False


def test_registry_default_null(monkeypatch):
    monkeypatch.delenv("CONVERGE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CONVERGE_LLM_API_KEY", raising=False)
    adapter = registry.get_adapter()
    assert adapter.provider_name == "null"
    assert adapter.is_available() is False


def test_registry_anthropic(monkeypatch):
    monkeypatch.setenv("CONVERGE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("CONVERGE_LLM_API_KEY", "test-key-123")

    mock_anthropic = MagicMock()
    with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
        adapter = registry.get_adapter()
        assert adapter.provider_name == "anthropic"


def test_rate_limit_allows():
    registry._call_timestamps.clear()
    assert registry.check_rate_limit() is True


def test_rate_limit_blocks(monkeypatch):
    import time
    monkeypatch.setattr(registry, "MAX_CALLS_PER_HOUR", 2)
    registry._call_timestamps.clear()
    registry.record_call()
    registry.record_call()
    assert registry.check_rate_limit() is False


def test_review_with_llm_shadow(db_path, monkeypatch):
    """In shadow mode, LLM analysis generates event but doesn't block."""
    from converge import event_log, feature_flags
    from converge.models import Event, EventType, Intent, RiskLevel, Status

    monkeypatch.setenv("CONVERGE_FF_LLM_REVIEW_ADVISOR", "1")
    monkeypatch.setenv("CONVERGE_FF_LLM_REVIEW_ADVISOR_MODE", "shadow")
    feature_flags.reload_flags()

    # Create an intent
    intent = Intent(id="llm-test-001", source="f/x", target="main", status=Status.READY)
    event_log.upsert_intent(intent)

    # Verify flag is enabled
    assert feature_flags.is_enabled("llm_review_advisor")
    assert feature_flags.get_mode("llm_review_advisor") == "shadow"




def test_review_with_llm_enforce(db_path, monkeypatch):
    """In enforce mode, analysis is generated and event emitted."""
    from converge import event_log, feature_flags
    from converge.models import Event, EventType, Intent, RiskLevel, Status

    monkeypatch.setenv("CONVERGE_FF_LLM_REVIEW_ADVISOR", "1")
    monkeypatch.setenv("CONVERGE_FF_LLM_REVIEW_ADVISOR_MODE", "enforce")
    feature_flags.reload_flags()

    assert feature_flags.get_mode("llm_review_advisor") == "enforce"




def test_review_llm_failure(db_path, monkeypatch):
    """If LLM adapter fails, REVIEW_ANALYSIS_FAILED event is emitted, review continues."""
    from converge import event_log, feature_flags
    from converge.models import Event, EventType, Intent, Status

    monkeypatch.setenv("CONVERGE_FF_LLM_REVIEW_ADVISOR", "1")
    monkeypatch.setenv("CONVERGE_FF_LLM_REVIEW_ADVISOR_MODE", "enforce")
    monkeypatch.setenv("CONVERGE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("CONVERGE_LLM_API_KEY", "test-key")
    feature_flags.reload_flags()

    intent = Intent(id="llm-fail-001", source="f/x", target="main", status=Status.READY)
    event_log.upsert_intent(intent)

    # Mock the adapter to raise
    mock_adapter = MagicMock()
    mock_adapter.is_available.return_value = True
    mock_adapter.provider_name = "anthropic"
    mock_adapter.analyze_review.side_effect = RuntimeError("API error")

    with patch("converge.llm.registry.get_adapter", return_value=mock_adapter), \
         patch("converge.llm.registry.check_rate_limit", return_value=True):
        from converge import reviews
        task = reviews.request_review(intent.id, trigger="manual")
        assert task is not None

    events = event_log.query(event_type=EventType.REVIEW_ANALYSIS_FAILED, intent_id=intent.id)
    assert len(events) == 1
    assert "API error" in events[0]["payload"]["error"]




def test_review_without_llm(db_path, monkeypatch):
    """With flag disabled, no LLM call happens."""
    from converge import event_log, feature_flags
    from converge.models import EventType, Intent, Status

    monkeypatch.setenv("CONVERGE_FF_LLM_REVIEW_ADVISOR", "0")
    feature_flags.reload_flags()

    intent = Intent(id="no-llm-001", source="f/x", target="main", status=Status.READY)
    event_log.upsert_intent(intent)

    from converge import reviews
    task = reviews.request_review(intent.id, trigger="manual")
    assert task is not None

    events = event_log.query(event_type=EventType.REVIEW_ANALYSIS_GENERATED, intent_id=intent.id)
    assert len(events) == 0


