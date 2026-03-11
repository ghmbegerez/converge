"""Tests for the extracted validation pipeline (converge.validation_pipeline).

These tests verify that the pipeline functions work correctly when called
directly, complementing the engine-level tests in test_engine.py.
"""

from unittest.mock import MagicMock, patch

from conftest import make_intent

from converge import event_log
from converge.models import (
    CoherenceEvaluation,
    EventType,
    PolicyEvaluation,
    PolicyVerdict,
    RiskEval,
    Simulation,
    Status,
)
from converge.validation_pipeline import (
    StepResult,
    _evaluate_risk_step,
    _finalize_validation,
    _resolve_simulation,
    block_intent,
    run_validation_pipeline,
)

# ---------------------------------------------------------------------------
# StepResult type alias
# ---------------------------------------------------------------------------

class TestStepResultType:
    def test_step_result_is_tuple_alias(self):
        """StepResult is a tuple[dict, bool] alias."""
        assert StepResult == tuple[dict, bool]


# ---------------------------------------------------------------------------
# run_validation_pipeline (the main orchestrator)
# ---------------------------------------------------------------------------

class TestRunValidationPipeline:
    def test_pipeline_validates_clean_intent(self, db_path):
        """Pipeline returns 'validated' for a mergeable, passing intent."""
        intent = make_intent("vp-001")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        result = run_validation_pipeline(intent, sim=sim, skip_checks=True)
        assert result["decision"] == "validated"
        assert result["intent_id"] == "vp-001"
        assert "trace_id" in result
        assert "risk" in result
        assert "policy" in result

    def test_pipeline_blocks_on_conflict(self, db_path):
        """Pipeline returns 'blocked' when simulation has conflicts."""
        intent = make_intent("vp-002")
        sim = Simulation(mergeable=False, conflicts=["file.py"], source="feature/x", target="main")

        result = run_validation_pipeline(intent, sim=sim, skip_checks=True)
        assert result["decision"] == "blocked"
        assert "conflict" in result["reason"].lower()

    def test_pipeline_blocks_on_failed_checks(self, db_path):
        """Pipeline blocks when subprocess checks fail."""
        intent = make_intent("vp-003")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        with patch("converge.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="lint error")
            result = run_validation_pipeline(intent, sim=sim, skip_checks=False)

        assert result["decision"] == "blocked"
        assert "checks failed" in result["reason"].lower()

    def test_pipeline_returns_trace_id(self, db_path):
        """Every pipeline result includes a trace_id."""
        intent = make_intent("vp-004")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        result = run_validation_pipeline(intent, sim=sim, skip_checks=True)
        assert result["trace_id"].startswith("trace-")

    def test_pipeline_produces_risk_event(self, db_path):
        """Pipeline emits a risk.evaluated event with 4 signals."""
        intent = make_intent("vp-005")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        run_validation_pipeline(intent, sim=sim, skip_checks=True)

        risk_events = event_log.query(event_type="risk.evaluated")
        assert len(risk_events) == 1
        evidence = risk_events[0]["evidence"]
        assert "signals" in evidence
        assert "entropic_load" in evidence["signals"]

    def test_pipeline_produces_policy_event(self, db_path):
        """Pipeline emits a policy.evaluated event."""
        intent = make_intent("vp-006")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        run_validation_pipeline(intent, sim=sim, skip_checks=True)

        policy_events = event_log.query(event_type="policy.evaluated")
        assert len(policy_events) == 1

    def test_pipeline_updates_intent_status_to_validated(self, db_path):
        """A successful pipeline run sets the intent to VALIDATED."""
        intent = make_intent("vp-007")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        run_validation_pipeline(intent, sim=sim, skip_checks=True)

        loaded = event_log.get_intent("vp-007")
        assert loaded.status == Status.VALIDATED


# ---------------------------------------------------------------------------
# _resolve_simulation step
# ---------------------------------------------------------------------------

class TestResolveSimulation:
    def test_uses_provided_simulation(self, db_path):
        """When a sim is passed directly, it is used as-is."""
        intent = make_intent("rs-001")
        sim = Simulation(mergeable=True, files_changed=["x.py"], source="f/a", target="main")

        result_sim, blocked = _resolve_simulation(intent, sim, False, None, "trace-test")
        assert blocked is None
        assert result_sim is sim

    def test_blocks_on_unmergeable(self, db_path):
        """An unmergeable simulation blocks the pipeline."""
        intent = make_intent("rs-002")
        sim = Simulation(mergeable=False, conflicts=["a.py"], source="f/a", target="main")

        result_sim, blocked = _resolve_simulation(intent, sim, False, None, "trace-test")
        assert result_sim is None
        assert blocked["decision"] == "blocked"
        assert "conflict" in blocked["reason"].lower()

    def test_use_last_simulation_returns_none_when_missing(self, db_path):
        """With use_last_simulation=True but no stored sim, blocks."""
        intent = make_intent("rs-003")
        # Provide a mock _engine that returns None for simulate_from_last
        mock_engine = MagicMock()
        mock_engine.simulate_from_last.return_value = None

        result_sim, blocked = _resolve_simulation(intent, None, True, None, "trace-test",
                                                   _engine=mock_engine)
        assert blocked is not None
        assert blocked["decision"] == "blocked"
        assert "no previous simulation" in blocked["reason"].lower()


# ---------------------------------------------------------------------------
# block_intent helper
# ---------------------------------------------------------------------------

class TestBlockHelper:
    def test_block_returns_blocked_decision(self, db_path):
        """block_intentproduces a decision dict with decision='blocked'."""
        intent = make_intent("bh-001")
        result = block_intent(intent, "test reason", trace_id="trace-test")
        assert result["decision"] == "blocked"
        assert result["reason"] == "test reason"
        assert result["trace_id"] == "trace-test"

    def test_block_records_event(self, db_path):
        """block_intentappends an intent.blocked event."""
        intent = make_intent("bh-002")
        block_intent(intent, "some failure", trace_id="trace-test")

        events = event_log.query(event_type=EventType.INTENT_BLOCKED)
        assert len(events) == 1
        assert events[0]["payload"]["reason"] == "some failure"

    def test_block_includes_simulation_when_provided(self, db_path):
        """When a sim is provided, block_intent includes it in the result."""
        intent = make_intent("bh-003")
        sim = Simulation(mergeable=False, conflicts=["x.py"], source="f/a", target="main")
        result = block_intent(intent, "conflict", sim=sim)
        assert "simulation" in result
        assert result["simulation"]["conflicts"] == ["x.py"]

    def test_block_includes_risk_when_provided(self, db_path):
        """When risk_eval is provided, block_intent includes it."""
        intent = make_intent("bh-004")
        risk_eval = RiskEval(intent_id="bh-004", risk_score=42.0)
        result = block_intent(intent, "too risky", risk_eval=risk_eval)
        assert "risk" in result
        assert result["risk"]["risk_score"] == 42.0

    def test_block_without_trace_id(self, db_path):
        """block_intentwithout trace_id does not include trace_id in result."""
        intent = make_intent("bh-005")
        result = block_intent(intent, "no trace")
        assert "trace_id" not in result


# ---------------------------------------------------------------------------
# _evaluate_risk_step
# ---------------------------------------------------------------------------

class TestEvaluateRiskStep:
    def test_risk_step_returns_risk_eval(self, db_path):
        """Risk step returns a RiskEval and emits an event."""
        intent = make_intent("risk-001")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="f/x", target="main")

        risk_eval = _evaluate_risk_step(intent, sim, None, "trace-test")
        assert isinstance(risk_eval, RiskEval)
        assert risk_eval.intent_id == "risk-001"

        events = event_log.query(event_type=EventType.RISK_EVALUATED)
        assert len(events) == 1


# ---------------------------------------------------------------------------
# _finalize_validation
# ---------------------------------------------------------------------------

class TestFinalizeValidation:
    def test_finalize_sets_validated_status(self, db_path):
        """Finalize marks intent VALIDATED and emits event."""
        intent = make_intent("fin-001")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="f/x", target="main")
        risk_eval = RiskEval(intent_id="fin-001", risk_score=10.0)
        policy_eval = PolicyEvaluation(verdict=PolicyVerdict.ALLOW, profile_used="medium")
        risk_gate = {"enforced": False, "breaches": []}

        result = _finalize_validation(intent, sim, risk_eval, policy_eval, risk_gate, "trace-fin")
        assert result["decision"] == "validated"
        assert result["trace_id"] == "trace-fin"

        loaded = event_log.get_intent("fin-001")
        assert loaded.status == Status.VALIDATED

    def test_finalize_includes_coherence_when_provided(self, db_path):
        """When coherence_eval is provided, finalize includes it."""
        intent = make_intent("fin-002")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="f/x", target="main")
        risk_eval = RiskEval(intent_id="fin-002", risk_score=5.0)
        policy_eval = PolicyEvaluation(verdict=PolicyVerdict.ALLOW, profile_used="low")
        risk_gate = {"enforced": False, "breaches": []}
        coherence_eval = CoherenceEvaluation(
            coherence_score=95.0, verdict="pass", results=[], harness_version="1.0",
        )

        result = _finalize_validation(
            intent, sim, risk_eval, policy_eval, risk_gate, "trace-fin",
            coherence_eval=coherence_eval,
        )
        assert "coherence" in result
        assert result["coherence"]["coherence_score"] == 95.0
