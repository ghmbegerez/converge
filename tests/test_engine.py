"""Tests for the core engine (3 invariants)."""

from unittest.mock import patch, MagicMock
import subprocess

from converge import engine, event_log
from converge.models import Event, EventType, Intent, RiskLevel, Simulation, Status
from converge.policy import PolicyConfig, DEFAULT_PROFILES


def _make_intent(db_path, **kw) -> Intent:
    defaults = dict(
        id="eng-001", source="feature/x", target="main",
        status=Status.READY, risk_level=RiskLevel.MEDIUM,
        priority=2, tenant_id="team-a",
    )
    defaults.update(kw)
    intent = Intent(**defaults)
    event_log.upsert_intent(db_path, intent)
    return intent


class TestValidateIntent:
    def test_validate_mergeable(self, db_path):
        intent = _make_intent(db_path)
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        result = engine.validate_intent(intent, db_path, sim=sim, skip_checks=True)
        assert result["decision"] == "validated"
        assert result["risk"]["risk_score"] >= 0

        # Should have recorded events
        events = event_log.query(db_path, event_type="intent.validated")
        assert len(events) == 1

    def test_validate_conflict_blocks(self, db_path):
        intent = _make_intent(db_path)
        sim = Simulation(mergeable=False, conflicts=["x.py"], source="feature/x", target="main")

        result = engine.validate_intent(intent, db_path, sim=sim, skip_checks=True)
        assert result["decision"] == "blocked"
        assert "conflict" in result["reason"].lower()

    def test_validate_records_risk_event(self, db_path):
        intent = _make_intent(db_path)
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        engine.validate_intent(intent, db_path, sim=sim, skip_checks=True)
        risk_events = event_log.query(db_path, event_type="risk.evaluated")
        assert len(risk_events) == 1
        # Verify 4 signals are in the payload
        payload = risk_events[0]["payload"]
        assert "signals" in payload
        assert "entropic_load" in payload["signals"]
        assert "contextual_value" in payload["signals"]
        # Verify evidence includes signals
        evidence = risk_events[0]["evidence"]
        assert "signals" in evidence

    def test_validate_records_policy_event(self, db_path):
        intent = _make_intent(db_path)
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        engine.validate_intent(intent, db_path, sim=sim, skip_checks=True)
        policy_events = event_log.query(db_path, event_type="policy.evaluated")
        assert len(policy_events) == 1

    def test_validate_returns_trace_id(self, db_path):
        """Validate returns a trace_id for forensic correlation."""
        intent = _make_intent(db_path)
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        result = engine.validate_intent(intent, db_path, sim=sim, skip_checks=True)
        assert "trace_id" in result
        assert result["trace_id"].startswith("trace-")

    def test_trace_id_propagated_to_events(self, db_path):
        """trace_id appears in risk and policy events — both evidence and SQL column."""
        intent = _make_intent(db_path)
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        result = engine.validate_intent(intent, db_path, sim=sim, skip_checks=True)
        trace_id = result["trace_id"]

        risk_events = event_log.query(db_path, event_type="risk.evaluated")
        assert risk_events[0]["evidence"]["trace_id"] == trace_id
        assert risk_events[0]["trace_id"] == trace_id  # SQL column

        policy_events = event_log.query(db_path, event_type="policy.evaluated")
        assert policy_events[0]["payload"]["trace_id"] == trace_id
        assert policy_events[0]["trace_id"] == trace_id  # SQL column

        validated_events = event_log.query(db_path, event_type="intent.validated")
        assert validated_events[0]["trace_id"] == trace_id  # SQL column

    def test_blocked_includes_trace_id(self, db_path):
        """Blocked decisions also include trace_id."""
        intent = _make_intent(db_path)
        sim = Simulation(mergeable=False, conflicts=["x.py"], source="feature/x", target="main")

        result = engine.validate_intent(intent, db_path, sim=sim, skip_checks=True)
        assert result["decision"] == "blocked"
        assert "trace_id" in result


class TestSimulateFromLast:
    def test_retrieve_last_simulation(self, db_path):
        event_log.append(db_path, Event(
            event_type="simulation.completed",
            intent_id="eng-001",
            payload={"mergeable": True, "conflicts": [], "files_changed": ["a.py"],
                     "source": "feature/x", "target": "main"},
        ))
        sim = engine.simulate_from_last(db_path, "eng-001")
        assert sim is not None
        assert sim.mergeable is True

    def test_no_simulation_returns_none(self, db_path):
        sim = engine.simulate_from_last(db_path, "nonexistent")
        assert sim is None


class TestProcessQueue:
    """Tests for Invariants 2 (revalidation) and 3 (bounded retry)."""

    def test_invariant3_max_retries_rejects(self, db_path):
        """Invariant 3: retries > max → REJECTED"""
        intent = _make_intent(db_path, id="q-001", status=Status.VALIDATED)
        # Set retries to max
        event_log.update_intent_status(db_path, "q-001", Status.VALIDATED, retries=3)

        results = engine.process_queue(
            db_path, max_retries=3, use_last_simulation=True, skip_checks=True
        )
        assert len(results) == 1
        assert results[0]["decision"] == "rejected"

        # Verify status changed
        loaded = event_log.get_intent(db_path, "q-001")
        assert loaded.status == Status.REJECTED

    def test_queue_lock_prevents_concurrent(self, db_path):
        """SQLite advisory lock prevents concurrent execution."""
        # Acquire the lock manually with a fake PID
        assert event_log.acquire_queue_lock(db_path, holder_pid=99999)

        results = engine.process_queue(db_path)
        assert len(results) == 1
        assert "error" in results[0]
        assert "lock" in results[0]

        # Release the lock
        event_log.release_queue_lock(db_path, holder_pid=99999)


class TestConfirmMerge:
    def test_confirm_queued_intent(self, db_path):
        _make_intent(db_path, id="m-001", status=Status.QUEUED)
        event_log.update_intent_status(db_path, "m-001", Status.QUEUED)

        result = engine.confirm_merge(db_path, "m-001", merged_commit="abc123")
        assert result["status"] == "MERGED"

        loaded = event_log.get_intent(db_path, "m-001")
        assert loaded.status == Status.MERGED

    def test_confirm_nonexistent(self, db_path):
        result = engine.confirm_merge(db_path, "nonexistent")
        assert "error" in result

    def test_confirm_wrong_status(self, db_path):
        _make_intent(db_path, id="m-002", status=Status.MERGED)
        event_log.update_intent_status(db_path, "m-002", Status.MERGED)

        result = engine.confirm_merge(db_path, "m-002")
        assert "error" in result


class TestResetAndInspect:
    def test_reset_retries(self, db_path):
        _make_intent(db_path, id="r-001", status=Status.READY)
        event_log.update_intent_status(db_path, "r-001", Status.READY, retries=5)

        result = engine.reset_queue(db_path, "r-001", set_status="VALIDATED")
        assert result["retries"] == 0
        assert result["status"] == "VALIDATED"

    def test_inspect_actionable(self, db_path):
        _make_intent(db_path, id="i-001", status=Status.READY)
        _make_intent(db_path, id="i-002", status=Status.VALIDATED)
        _make_intent(db_path, id="i-003", status=Status.MERGED)

        result = engine.inspect_queue(db_path, only_actionable=True)
        ids = [r["intent_id"] for r in result]
        assert "i-001" in ids
        assert "i-002" in ids
        assert "i-003" not in ids


class TestChecksForRiskLevel:
    """checks_for_risk_level() returns required checks per risk profile."""

    def test_low_risk_requires_lint(self):
        cfg = PolicyConfig(profiles=dict(DEFAULT_PROFILES), queue={}, risk={})
        checks = engine.checks_for_risk_level(RiskLevel.LOW, config=cfg)
        assert checks == ["lint"]

    def test_high_risk_requires_lint_and_unit_tests(self):
        cfg = PolicyConfig(profiles=dict(DEFAULT_PROFILES), queue={}, risk={})
        checks = engine.checks_for_risk_level(RiskLevel.HIGH, config=cfg)
        assert "lint" in checks
        assert "unit_tests" in checks

    def test_critical_risk_requires_lint_and_unit_tests(self):
        cfg = PolicyConfig(profiles=dict(DEFAULT_PROFILES), queue={}, risk={})
        checks = engine.checks_for_risk_level(RiskLevel.CRITICAL, config=cfg)
        assert "lint" in checks
        assert "unit_tests" in checks

    def test_unknown_risk_falls_back_to_medium(self):
        cfg = PolicyConfig(profiles=dict(DEFAULT_PROFILES), queue={}, risk={})
        checks = engine.checks_for_risk_level("nonexistent", config=cfg)
        assert checks == ["lint"]  # medium profile fallback


class TestRunChecks:
    """run_checks() executes subprocess commands and records events."""

    def test_passing_check_records_event(self, db_path):
        """A check that passes returns passed=True and records a check.completed event."""
        with patch("converge.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            results = engine.run_checks(["lint"], db_path, intent_id="chk-001")

        assert len(results) == 1
        assert results[0].check_type == "lint"
        assert results[0].passed is True
        assert results[0].details == "OK"

        events = event_log.query(db_path, event_type=EventType.CHECK_COMPLETED)
        assert len(events) == 1
        assert events[0]["payload"]["check_type"] == "lint"
        assert events[0]["payload"]["passed"] is True

    def test_failing_check_records_failure(self, db_path):
        """A check that fails returns passed=False with stderr as details."""
        with patch("converge.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error: lint failed")
            results = engine.run_checks(["lint"], db_path, intent_id="chk-002")

        assert len(results) == 1
        assert results[0].passed is False
        assert "lint failed" in results[0].details

    def test_timeout_records_failure(self, db_path):
        """A check that times out is recorded as failed."""
        with patch("converge.engine.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="make lint", timeout=300)
            results = engine.run_checks(["lint"], db_path, intent_id="chk-003")

        assert len(results) == 1
        assert results[0].passed is False
        assert "timed out" in results[0].details.lower()

    def test_unsupported_check_skipped(self, db_path):
        """Checks not in SUPPORTED_CHECKS are silently skipped."""
        results = engine.run_checks(["unknown_check_type"], db_path)
        assert len(results) == 0

    def test_multiple_checks_all_recorded(self, db_path):
        """Multiple checks each produce their own event."""
        with patch("converge.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            results = engine.run_checks(["lint", "unit_tests"], db_path, intent_id="chk-004")

        assert len(results) == 2
        assert results[0].check_type == "lint"
        assert results[1].check_type == "unit_tests"

        events = event_log.query(db_path, event_type=EventType.CHECK_COMPLETED)
        assert len(events) == 2

    def test_file_not_found_records_failure(self, db_path):
        """Missing command binary is caught and recorded as failure."""
        with patch("converge.engine.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("make not found")
            results = engine.run_checks(["lint"], db_path, intent_id="chk-005")

        assert len(results) == 1
        assert results[0].passed is False


class TestValidateWithChecks:
    """validate_intent with skip_checks=False — Invariant 1 full path."""

    def test_validate_passes_when_checks_pass(self, db_path):
        """Invariant 1 full: mergeable ∧ checks_pass → validated."""
        intent = _make_intent(db_path, id="vc-001")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        with patch("converge.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            result = engine.validate_intent(intent, db_path, sim=sim, skip_checks=False)

        assert result["decision"] == "validated"

        # Check events were recorded
        check_events = event_log.query(db_path, event_type=EventType.CHECK_COMPLETED)
        assert len(check_events) >= 1

    def test_validate_blocks_when_check_fails(self, db_path):
        """Invariant 1: mergeable ∧ ¬checks_pass → blocked."""
        intent = _make_intent(db_path, id="vc-002")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        with patch("converge.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="lint error")
            result = engine.validate_intent(intent, db_path, sim=sim, skip_checks=False)

        assert result["decision"] == "blocked"
        assert "checks failed" in result["reason"].lower()

    def test_trace_id_propagated_to_simulation_and_checks(self, db_path):
        """trace_id flows from validate_intent to simulation.completed and check.completed events."""
        intent = _make_intent(db_path, id="vc-trace")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        with patch("converge.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            result = engine.validate_intent(intent, db_path, sim=sim, skip_checks=False)

        trace_id = result["trace_id"]

        # Check events carry the same trace_id
        check_events = event_log.query(db_path, event_type=EventType.CHECK_COMPLETED, intent_id="vc-trace")
        assert len(check_events) >= 1
        assert check_events[0]["trace_id"] == trace_id

        # Risk and policy events too
        risk_events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, intent_id="vc-trace")
        assert risk_events[0]["trace_id"] == trace_id

        policy_events = event_log.query(db_path, event_type=EventType.POLICY_EVALUATED, intent_id="vc-trace")
        assert policy_events[0]["trace_id"] == trace_id

    def test_validate_high_risk_blocks_without_all_checks(self, db_path):
        """HIGH risk requires lint+unit_tests. Passing only lint → blocked."""
        intent = _make_intent(db_path, id="vc-003", risk_level=RiskLevel.HIGH)
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # lint passes
                return MagicMock(returncode=0, stdout="OK", stderr="")
            else:  # unit_tests fails
                return MagicMock(returncode=1, stdout="", stderr="test failure")

        with patch("converge.engine.subprocess.run", side_effect=side_effect):
            result = engine.validate_intent(intent, db_path, sim=sim, skip_checks=False)

        assert result["decision"] == "blocked"
        assert "checks failed" in result["reason"].lower()
