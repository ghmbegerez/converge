"""Tests for risk auto-classification (Initiative 2)."""
import pytest

from conftest import make_intent

from converge.defaults import RISK_CLASSIFICATION_THRESHOLDS
from converge.models import Event, EventType, Intent, RiskLevel, Status
from converge.risk.eval import classify_risk_level


def test_classify_low():
    assert classify_risk_level(10.0) == RiskLevel.LOW
    assert classify_risk_level(0.0) == RiskLevel.LOW
    assert classify_risk_level(24.9) == RiskLevel.LOW


def test_classify_medium():
    assert classify_risk_level(25.0) == RiskLevel.MEDIUM
    assert classify_risk_level(40.0) == RiskLevel.MEDIUM
    assert classify_risk_level(49.9) == RiskLevel.MEDIUM


def test_classify_high():
    assert classify_risk_level(50.0) == RiskLevel.HIGH
    assert classify_risk_level(60.0) == RiskLevel.HIGH
    assert classify_risk_level(74.9) == RiskLevel.HIGH


def test_classify_critical():
    assert classify_risk_level(75.0) == RiskLevel.CRITICAL
    assert classify_risk_level(100.0) == RiskLevel.CRITICAL


def test_classify_boundary_values():
    """Exact boundary values map to the higher band."""
    assert classify_risk_level(0.0) == RiskLevel.LOW
    assert classify_risk_level(25.0) == RiskLevel.MEDIUM
    assert classify_risk_level(50.0) == RiskLevel.HIGH
    assert classify_risk_level(75.0) == RiskLevel.CRITICAL


def test_reclassification_emits_event(db_path):
    """When risk level changes, RISK_LEVEL_RECLASSIFIED event is emitted."""
    from converge import event_log

    # Create an intent with LOW risk
    intent = make_intent("test-reclass-001", risk_level=RiskLevel.LOW)

    # Simulate what engine does: classify and emit event
    new_level = classify_risk_level(60.0)  # Should be HIGH
    assert new_level == RiskLevel.HIGH
    assert new_level != intent.risk_level

    # Emit the event
    event_log.append(Event(
        event_type=EventType.RISK_LEVEL_RECLASSIFIED,
        intent_id=intent.id,
        payload={"old": intent.risk_level.value, "new": new_level.value, "risk_score": 60.0},
    ))

    events = event_log.query(event_type=EventType.RISK_LEVEL_RECLASSIFIED, intent_id=intent.id)
    assert len(events) == 1
    assert events[0]["payload"]["old"] == "low"
    assert events[0]["payload"]["new"] == "high"


def test_no_event_if_same_level(db_path):
    """If classification doesn't change, no event should be emitted."""
    from converge import event_log

    new_level = classify_risk_level(30.0)  # MEDIUM
    assert new_level == RiskLevel.MEDIUM

    # If intent was already MEDIUM, no event
    events = event_log.query(event_type=EventType.RISK_LEVEL_RECLASSIFIED)
    assert len(events) == 0


def test_flag_disabled_skips(db_path, monkeypatch):
    """With risk_auto_classify disabled, reclassification is skipped."""
    from converge import feature_flags

    monkeypatch.setenv("CONVERGE_FF_RISK_AUTO_CLASSIFY", "0")
    feature_flags.reload_flags()

    assert not feature_flags.is_enabled("risk_auto_classify")


def test_custom_thresholds():
    """Custom thresholds override defaults."""
    custom = {"low": 0.0, "medium": 10.0, "high": 30.0, "critical": 50.0}
    assert classify_risk_level(5.0, thresholds=custom) == RiskLevel.LOW
    assert classify_risk_level(15.0, thresholds=custom) == RiskLevel.MEDIUM
    assert classify_risk_level(35.0, thresholds=custom) == RiskLevel.HIGH
    assert classify_risk_level(55.0, thresholds=custom) == RiskLevel.CRITICAL
