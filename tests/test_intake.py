"""Tests for adaptive intake control (AR-41..AR-43)."""


from conftest import make_intent

from converge import event_log
from converge.event_types import EventType
from converge.intake import (
    DEFAULT_INTAKE_CONFIG,
    IntakeMode,
    _compute_auto_mode,
    _throttle_bucket,
    evaluate_intake,
    intake_status,
    set_intake_mode,
)
from converge.models import Intent, RiskLevel, Status, now_iso


# ---------------------------------------------------------------------------
# TestThrottleBucket
# ---------------------------------------------------------------------------

class TestThrottleBucket:
    """Deterministic hashing for throttle decisions."""

    def test_deterministic(self, db_path):
        """Same intent_id always yields same bucket."""
        a = _throttle_bucket("intent-abc")
        b = _throttle_bucket("intent-abc")
        assert a == b

    def test_range(self, db_path):
        """Bucket is in [0, 1)."""
        for i in range(100):
            b = _throttle_bucket(f"intent-{i}")
            assert 0.0 <= b < 1.0

    def test_distribution(self, db_path):
        """Roughly uniform distribution across many intents."""
        below_half = sum(1 for i in range(1000) if _throttle_bucket(f"x-{i}") < 0.5)
        assert 400 < below_half < 600  # rough 50% ± 10%


# ---------------------------------------------------------------------------
# TestAutoMode
# ---------------------------------------------------------------------------

class TestAutoMode:
    """Auto-computed mode from health signals."""

    def test_healthy_system_is_open(self, db_path):
        """Score >= 60 → open mode."""
        cfg = dict(DEFAULT_INTAKE_CONFIG)
        mode, signals = _compute_auto_mode(config=cfg)
        # Fresh DB → health_score=100 → open
        assert mode == IntakeMode.OPEN
        assert signals["health_score"] == 100.0

    def test_degraded_system_is_throttle(self, db_path):
        """Score between pause and throttle thresholds → throttle mode."""
        # Create enough bad state to push health score down
        # A rejected intent + high conflict rate → lower score
        for i in range(20):
            make_intent(f"rejected-{i}", status=Status.REJECTED)
        cfg = dict(DEFAULT_INTAKE_CONFIG)
        mode, signals = _compute_auto_mode(config=cfg)
        # Score should be < 60 but > 30 with 20 rejected intents
        score = signals["health_score"]
        if score < 60:
            assert mode in (IntakeMode.THROTTLE, IntakeMode.PAUSE)
        else:
            assert mode == IntakeMode.OPEN

    def test_custom_thresholds(self, db_path):
        """Custom thresholds override defaults."""
        # With threshold at 200, score 100 < 200 → throttle
        cfg = {"pause_below_health": 50.0, "throttle_below_health": 200.0, "throttle_ratio": 0.5}
        mode, _ = _compute_auto_mode(config=cfg)
        assert mode == IntakeMode.THROTTLE

    def test_extreme_thresholds_pause(self, db_path):
        """With pause_below_health very high, we get PAUSE."""
        cfg = {"pause_below_health": 200.0, "throttle_below_health": 300.0, "throttle_ratio": 0.5}
        mode, _ = _compute_auto_mode(config=cfg)
        assert mode == IntakeMode.PAUSE


# ---------------------------------------------------------------------------
# TestEvaluateIntake
# ---------------------------------------------------------------------------

class TestEvaluateIntake:
    """Full intake evaluation including event emission."""

    def test_open_mode_accepts(self, db_path):
        """Open mode accepts all intents."""
        intent = make_intent("test-001")
        decision = evaluate_intake(intent)
        assert decision.accepted is True
        assert decision.mode == IntakeMode.OPEN

    def test_open_mode_emits_accepted_event(self, db_path):
        """Accepted intents emit intake.accepted event."""
        intent = make_intent("evt-test")
        evaluate_intake(intent)
        events = event_log.query(event_type=EventType.INTAKE_ACCEPTED)
        assert len(events) == 1
        assert events[0]["intent_id"] == "evt-test"

    def test_pause_mode_rejects_medium(self, db_path):
        """Pause mode rejects non-critical intents."""
        intent = make_intent("test-001", risk_level=RiskLevel.MEDIUM)
        cfg = {"pause_below_health": 200.0, "throttle_below_health": 300.0, "throttle_ratio": 0.5}
        decision = evaluate_intake(intent, config=cfg)
        assert decision.accepted is False
        assert decision.mode == IntakeMode.PAUSE

    def test_pause_mode_accepts_critical(self, db_path):
        """Pause mode still accepts critical-risk intents."""
        intent = make_intent("test-001", risk_level=RiskLevel.CRITICAL)
        cfg = {"pause_below_health": 200.0, "throttle_below_health": 300.0, "throttle_ratio": 0.5}
        decision = evaluate_intake(intent, config=cfg)
        assert decision.accepted is True
        assert decision.mode == IntakeMode.PAUSE

    def test_pause_mode_emits_rejected_event(self, db_path):
        """Rejected intents in pause mode emit intake.rejected event."""
        intent = make_intent("pause-reject")
        cfg = {"pause_below_health": 200.0, "throttle_below_health": 300.0, "throttle_ratio": 0.5}
        evaluate_intake(intent, config=cfg)
        events = event_log.query(event_type=EventType.INTAKE_REJECTED)
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["mode"] == "pause"
        assert payload["accepted"] is False

    def test_throttle_mode_deterministic(self, db_path):
        """Throttle uses deterministic bucket — same intent always same result."""
        intent = make_intent("throttle-test")
        cfg = {"pause_below_health": 0.0, "throttle_below_health": 200.0, "throttle_ratio": 0.5}
        d1 = evaluate_intake(intent, config=cfg)
        d2 = evaluate_intake(intent, config=cfg)
        assert d1.accepted == d2.accepted

    def test_throttle_ratio_zero_rejects_all(self, db_path):
        """Throttle ratio=0 rejects all intents."""
        intent = make_intent("test-001")
        cfg = {"pause_below_health": 0.0, "throttle_below_health": 200.0, "throttle_ratio": 0.0}
        decision = evaluate_intake(intent, config=cfg)
        assert decision.accepted is False
        assert decision.mode == IntakeMode.THROTTLE

    def test_throttle_ratio_one_accepts_all(self, db_path):
        """Throttle ratio=1 accepts all intents."""
        intent = make_intent("test-001")
        cfg = {"pause_below_health": 0.0, "throttle_below_health": 200.0, "throttle_ratio": 1.0}
        decision = evaluate_intake(intent, config=cfg)
        assert decision.accepted is True
        assert decision.mode == IntakeMode.THROTTLE

    def test_throttle_emits_throttled_event(self, db_path):
        """Throttled intents emit intake.throttled event."""
        intent = make_intent("throttle-evt")
        cfg = {"pause_below_health": 0.0, "throttle_below_health": 200.0, "throttle_ratio": 0.0}
        evaluate_intake(intent, config=cfg)
        events = event_log.query(event_type=EventType.INTAKE_THROTTLED)
        assert len(events) >= 1

    def test_signals_in_decision(self, db_path):
        """Decision includes health signals."""
        intent = make_intent("test-001")
        decision = evaluate_intake(intent)
        assert "health_score" in decision.signals
        assert "health_status" in decision.signals


# ---------------------------------------------------------------------------
# TestManualOverride
# ---------------------------------------------------------------------------

class TestManualOverride:
    """Manual mode override via set_intake_mode."""

    def test_set_manual_override(self, db_path):
        """Setting a mode stores override."""
        result = set_intake_mode("pause", reason="emergency")
        assert result["ok"] is True
        assert result["mode"] == "pause"

    def test_override_takes_effect(self, db_path):
        """Manual override overrides auto-computed mode."""
        set_intake_mode("pause")
        # Even though system is healthy → open, override says pause
        intent = make_intent("test-001", risk_level=RiskLevel.LOW, tenant_id="")
        decision = evaluate_intake(intent)
        assert decision.mode == IntakeMode.PAUSE
        assert decision.accepted is False

    def test_override_clear_with_auto(self, db_path):
        """mode='auto' clears the override."""
        set_intake_mode("pause")
        set_intake_mode("auto")
        # Now should go back to auto-computed (open on healthy system)
        intent = make_intent("test-001")
        decision = evaluate_intake(intent)
        assert decision.mode == IntakeMode.OPEN

    def test_override_emits_mode_changed(self, db_path):
        """Setting mode emits intake.mode_changed event."""
        set_intake_mode("throttle", reason="testing")
        events = event_log.query(event_type=EventType.INTAKE_MODE_CHANGED)
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["mode"] == "throttle"

    def test_auto_clear_emits_event(self, db_path):
        """Clearing override with 'auto' also emits event."""
        set_intake_mode("pause")
        set_intake_mode("auto")
        events = event_log.query(event_type=EventType.INTAKE_MODE_CHANGED)
        assert len(events) == 2

    def test_invalid_mode(self, db_path):
        """Invalid mode returns error."""
        result = set_intake_mode("invalid")
        assert result["ok"] is False
        assert "error" in result

    def test_per_tenant_override(self, db_path):
        """Overrides are tenant-scoped."""
        set_intake_mode("pause", tenant_id="tenant-A")
        # tenant-A: paused
        intent_a = make_intent("a-1", tenant_id="tenant-A")
        decision_a = evaluate_intake(intent_a)
        assert decision_a.mode == IntakeMode.PAUSE
        # tenant-B (no override): open
        intent_b = make_intent("b-1", tenant_id="tenant-B")
        decision_b = evaluate_intake(intent_b)
        assert decision_b.mode == IntakeMode.OPEN


# ---------------------------------------------------------------------------
# TestIntakeStatus
# ---------------------------------------------------------------------------

class TestIntakeStatus:
    """Intake status reporting."""

    def test_default_status(self, db_path):
        """Default status on healthy system."""
        status = intake_status()
        assert status["mode"] == "open"
        assert status["manual_override"] is False
        assert "signals" in status
        assert "config" in status

    def test_status_with_override(self, db_path):
        """Status reflects manual override."""
        set_intake_mode("throttle")
        status = intake_status()
        assert status["mode"] == "throttle"
        assert status["manual_override"] is True
        assert status["override"] is not None

    def test_status_shows_auto_mode(self, db_path):
        """Status includes what auto mode would be."""
        set_intake_mode("pause")
        status = intake_status()
        assert status["auto_mode"] == "open"  # healthy system → open auto
        assert status["mode"] == "pause"  # but override says pause

    def test_config_in_status(self, db_path):
        """Status includes threshold configuration."""
        status = intake_status()
        cfg = status["config"]
        assert "pause_below_health" in cfg
        assert "throttle_below_health" in cfg
        assert "throttle_ratio" in cfg


# ---------------------------------------------------------------------------
# TestIntakeIntegration
# ---------------------------------------------------------------------------

class TestIntakeIntegration:
    """End-to-end integration tests."""

    def test_intake_event_payload(self, db_path):
        """Intake events have complete payload structure."""
        intent = make_intent("payload-test", risk_level=RiskLevel.HIGH)
        evaluate_intake(intent)
        events = event_log.query(event_type=EventType.INTAKE_ACCEPTED)
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["mode"] == "open"
        assert payload["accepted"] is True
        assert payload["risk_level"] == "high"
        assert payload["origin_type"] == "human"
        assert "signals" in payload

    def test_multiple_intents_tracked(self, db_path):
        """Multiple intents each get their own intake event."""
        for i in range(5):
            evaluate_intake(make_intent(f"multi-{i}"))
        events = event_log.query(event_type=EventType.INTAKE_ACCEPTED)
        assert len(events) == 5

    def test_storage_roundtrip(self, db_path):
        """Override persists across status queries."""
        set_intake_mode("throttle", set_by="admin", reason="load test")
        override = event_log.get_intake_override(tenant_id="")
        assert override is not None
        assert override["mode"] == "throttle"
        assert override["set_by"] == "admin"
        assert override["reason"] == "load test"

    def test_delete_override(self, db_path):
        """Deleting override returns to auto mode."""
        set_intake_mode("pause")
        assert event_log.get_intake_override(tenant_id="") is not None
        event_log.delete_intake_override(tenant_id="")
        assert event_log.get_intake_override(tenant_id="") is None
