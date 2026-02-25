"""Tests for the coherence harness (systemic coherence evaluation)."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from conftest import make_intent

from converge import coherence, engine, event_log
from converge.models import (
    CoherenceEvaluation,
    CoherenceQuestion,
    CoherenceResult,
    CoherenceVerdict,
    Event,
    EventType,
    GateName,
    Intent,
    PolicyVerdict,
    RiskEval,
    RiskLevel,
    Simulation,
    Status,
)
from converge.policy import PolicyConfig, evaluate as policy_evaluate
from converge.defaults import DEFAULT_PROFILES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config():
    return PolicyConfig(
        profiles=dict(DEFAULT_PROFILES),
        queue={"max_retries": 3},
        risk={"max_risk_score": 65.0, "max_damage_score": 60.0, "max_propagation_score": 55.0},
    )


def _make_questions():
    return [
        CoherenceQuestion(
            id="q-test-count",
            question="Has the test file count decreased?",
            check="echo 10",
            assertion="result >= baseline",
            severity="high",
            category="structural",
        ),
        CoherenceQuestion(
            id="q-no-errors",
            question="Are there zero critical errors?",
            check="echo 0",
            assertion="result == 0",
            severity="critical",
            category="health",
        ),
    ]


def _risk_eval(**kw):
    defaults = dict(
        intent_id="test-001",
        risk_score=20.0,
        damage_score=10.0,
        entropy_score=5.0,
        propagation_score=10.0,
        containment_score=0.8,
    )
    defaults.update(kw)
    return RiskEval(**defaults)


# ---------------------------------------------------------------------------
# Phase 1: Loading questions
# ---------------------------------------------------------------------------

class TestLoadQuestions:
    def test_load_questions_from_json(self, tmp_path):
        """Load questions from a valid JSON config file."""
        config = {
            "version": "1.0.0",
            "questions": [
                {
                    "id": "q-test-count",
                    "question": "Has the test count decreased?",
                    "check": "echo 5",
                    "assertion": "result >= baseline",
                    "severity": "high",
                    "category": "structural",
                },
                {
                    "id": "q-lint",
                    "question": "Are there lint errors?",
                    "check": "echo 0",
                    "assertion": "result == 0",
                    "severity": "medium",
                    "category": "health",
                },
            ],
        }
        config_path = tmp_path / "coherence_harness.json"
        config_path.write_text(json.dumps(config))

        questions = coherence.load_questions(path=config_path)
        assert len(questions) == 2
        assert questions[0].id == "q-test-count"
        assert questions[0].severity == "high"
        assert questions[1].id == "q-lint"

    def test_load_questions_missing_file(self):
        """Missing config file returns empty list."""
        questions = coherence.load_questions(path="/nonexistent/path.json")
        assert questions == []

    def test_load_harness_version(self, tmp_path):
        config = {"version": "2.1.0", "questions": []}
        config_path = tmp_path / "coherence_harness.json"
        config_path.write_text(json.dumps(config))

        version = coherence.load_harness_version(path=config_path)
        assert version == "2.1.0"

    def test_load_harness_version_missing_file(self):
        version = coherence.load_harness_version(path="/nonexistent/path.json")
        assert version == "none"


# ---------------------------------------------------------------------------
# Phase 1: Running questions
# ---------------------------------------------------------------------------

class TestRunQuestion:
    def test_run_question_passes(self):
        """A check that satisfies its assertion passes."""
        q = CoherenceQuestion(
            id="q-pass",
            question="Does it pass?",
            check="echo 5",
            assertion="result >= 3",
            severity="high",
        )
        result = coherence.run_question(q)
        assert result.verdict == "pass"
        assert result.value == 5.0
        assert result.error is None

    def test_run_question_fails(self):
        """A check that violates its assertion fails."""
        q = CoherenceQuestion(
            id="q-fail",
            question="Does it fail?",
            check="echo 2",
            assertion="result >= 5",
            severity="high",
        )
        result = coherence.run_question(q)
        assert result.verdict == "fail"
        assert result.value == 2.0

    def test_run_question_with_baseline(self):
        """Assertion against baseline passes when met."""
        q = CoherenceQuestion(
            id="q-baseline",
            question="Baseline test",
            check="echo 10",
            assertion="result >= baseline",
            severity="high",
        )
        result = coherence.run_question(q, baselines={"q-baseline": 8.0})
        assert result.verdict == "pass"
        assert result.baseline == 8.0

    def test_run_question_baseline_missing_passes(self):
        """If assertion references baseline but none exists, pass (no baseline yet)."""
        q = CoherenceQuestion(
            id="q-no-baseline",
            question="No baseline yet",
            check="echo 10",
            assertion="result >= baseline",
            severity="high",
        )
        result = coherence.run_question(q, baselines={})
        assert result.verdict == "pass"

    def test_run_question_command_error(self):
        """A command that fails produces an error result."""
        q = CoherenceQuestion(
            id="q-error",
            question="Will fail?",
            check="exit 1",
            assertion="result == 0",
            severity="high",
        )
        result = coherence.run_question(q)
        assert result.verdict == "fail"
        assert result.error is not None

    def test_run_question_timeout(self):
        """A command that exceeds timeout produces an error result."""
        q = CoherenceQuestion(
            id="q-timeout",
            question="Timeout?",
            check="sleep 100",
            assertion="result == 0",
            severity="high",
        )
        with patch.object(coherence, "QUESTION_TIMEOUT_SECONDS", 0.1):
            result = coherence.run_question(q)
        assert result.verdict == "fail"
        assert result.error is not None
        assert "timed out" in result.error.lower()


# ---------------------------------------------------------------------------
# Phase 1: Scoring and evaluation
# ---------------------------------------------------------------------------

class TestEvaluateScoring:
    def test_evaluate_scoring_all_pass(self):
        """All questions pass → score 100, verdict pass."""
        questions = [
            CoherenceQuestion(id="q1", question="Q1", check="echo 0",
                              assertion="result == 0", severity="high"),
            CoherenceQuestion(id="q2", question="Q2", check="echo 5",
                              assertion="result >= 3", severity="medium"),
        ]
        result = coherence.evaluate(questions, baselines={})
        assert result.coherence_score == 100.0
        assert result.verdict == "pass"
        assert all(r.verdict == "pass" for r in result.results)

    def test_evaluate_scoring_one_fail_high(self):
        """One high-severity failure → score 80 (100 - 20)."""
        questions = [
            CoherenceQuestion(id="q1", question="Q1", check="echo 99",
                              assertion="result == 0", severity="high"),  # fails
        ]
        result = coherence.evaluate(questions, baselines={})
        assert result.coherence_score == 80.0
        assert result.verdict == "pass"  # 80 >= 75

    def test_evaluate_scoring_one_fail_critical(self):
        """One critical failure → score 70 (100 - 30)."""
        questions = [
            CoherenceQuestion(id="q1", question="Q1", check="echo 99",
                              assertion="result == 0", severity="critical"),  # fails
        ]
        result = coherence.evaluate(questions, baselines={})
        assert result.coherence_score == 70.0
        assert result.verdict == "warn"  # 60 <= 70 < 75

    def test_evaluate_scoring_multiple_failures(self):
        """Multiple failures accumulate penalties."""
        questions = [
            CoherenceQuestion(id="q1", question="Q1", check="echo 99",
                              assertion="result == 0", severity="critical"),  # -30
            CoherenceQuestion(id="q2", question="Q2", check="echo 99",
                              assertion="result == 0", severity="high"),     # -20
        ]
        result = coherence.evaluate(questions, baselines={})
        assert result.coherence_score == 50.0  # 100 - 30 - 20
        assert result.verdict == "fail"  # 50 < 60

    def test_evaluate_empty_questions(self):
        """No questions → score 100, verdict pass (backward compatible)."""
        result = coherence.evaluate([])
        assert result.coherence_score == 100.0
        assert result.verdict == "pass"
        assert result.results == []

    def test_evaluate_score_clamped_to_zero(self):
        """Score never goes below 0."""
        questions = [
            CoherenceQuestion(id=f"q{i}", question=f"Q{i}", check="echo 99",
                              assertion="result == 0", severity="critical")
            for i in range(5)  # 5 * 30 = 150 penalty
        ]
        result = coherence.evaluate(questions, baselines={})
        assert result.coherence_score == 0.0
        assert result.verdict == "fail"

    def test_evaluate_custom_thresholds(self):
        """Custom thresholds override defaults."""
        questions = [
            CoherenceQuestion(id="q1", question="Q1", check="echo 99",
                              assertion="result == 0", severity="high"),  # -20 → score 80
        ]
        result = coherence.evaluate(questions, baselines={}, pass_threshold=90, warn_threshold=85)
        assert result.coherence_score == 80.0
        assert result.verdict == "fail"  # 80 < 85


# ---------------------------------------------------------------------------
# Phase 1: Policy coherence gate
# ---------------------------------------------------------------------------

class TestPolicyCoherenceGate:
    def test_policy_coherence_gate_pass(self):
        """Score > 75 → gate passes."""
        result = policy_evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=10.0,
            containment_score=0.7,
            coherence_score=80.0,
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.ALLOW
        coherence_gate = next(g for g in result.gates if g.gate == GateName.COHERENCE)
        assert coherence_gate.passed

    def test_policy_coherence_gate_warn(self):
        """Score 60-75 → gate passes (warn handled upstream in engine)."""
        result = policy_evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=10.0,
            containment_score=0.7,
            coherence_score=65.0,
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.ALLOW
        coherence_gate = next(g for g in result.gates if g.gate == GateName.COHERENCE)
        assert coherence_gate.passed  # warn zone still passes the gate
        assert "warn zone" in coherence_gate.reason

    def test_policy_coherence_gate_fail(self):
        """Score < 60 → gate blocks."""
        result = policy_evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=10.0,
            containment_score=0.7,
            coherence_score=50.0,
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.BLOCK
        coherence_gate = next(g for g in result.gates if g.gate == GateName.COHERENCE)
        assert not coherence_gate.passed

    def test_policy_no_coherence_score(self):
        """When coherence_score is None, gate is not added (backward compat)."""
        result = policy_evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=10.0,
            containment_score=0.7,
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.ALLOW
        gate_names = [g.gate for g in result.gates]
        assert GateName.COHERENCE not in gate_names

    def test_critical_profile_stricter_thresholds(self):
        """Critical risk level uses stricter coherence thresholds (warn=70 for critical)."""
        result = policy_evaluate(
            risk_level=RiskLevel.CRITICAL,
            checks_passed=["lint", "unit_tests"],
            entropy_delta=5.0,
            containment_score=0.9,
            coherence_score=68.0,  # above default 60 but below critical warn=70
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.BLOCK
        coherence_gate = next(g for g in result.gates if g.gate == GateName.COHERENCE)
        assert not coherence_gate.passed


# ---------------------------------------------------------------------------
# Phase 3: Consistency cross-validation
# ---------------------------------------------------------------------------

class TestConsistencyCheck:
    def test_consistency_mismatch(self):
        """Coherence high + risk high → score mismatch inconsistency."""
        coh = CoherenceEvaluation(
            coherence_score=80.0,
            verdict="pass",
            results=[
                CoherenceResult(question_id="q1", question="Q1", verdict="pass",
                                value=5.0, baseline=3.0, assertion="result >= baseline"),
            ],
            harness_version="1.0.0",
        )
        risk = _risk_eval(risk_score=55.0)

        inconsistencies = coherence.check_consistency(coh, risk)
        assert len(inconsistencies) >= 1
        types = [i["type"] for i in inconsistencies]
        assert "score_mismatch" in types

    def test_consistency_clean(self):
        """Coherence and risk both low → no inconsistencies."""
        coh = CoherenceEvaluation(
            coherence_score=90.0,
            verdict="pass",
            results=[
                CoherenceResult(question_id="q1", question="Q1", verdict="pass",
                                value=5.0, baseline=3.0, assertion="result >= baseline"),
            ],
            harness_version="1.0.0",
        )
        risk = _risk_eval(risk_score=15.0)

        inconsistencies = coherence.check_consistency(coh, risk)
        assert len(inconsistencies) == 0

    def test_bomb_undetected(self):
        """All questions pass but bombs detected → inconsistency."""
        coh = CoherenceEvaluation(
            coherence_score=100.0,
            verdict="pass",
            results=[
                CoherenceResult(question_id="q1", question="Q1", verdict="pass",
                                value=0.0, baseline=None, assertion="result == 0"),
            ],
            harness_version="1.0.0",
        )
        risk = _risk_eval(bombs=[{"type": "complexity_spike"}])

        inconsistencies = coherence.check_consistency(coh, risk)
        types = [i["type"] for i in inconsistencies]
        assert "bomb_undetected" in types

    def test_missing_scope_validation(self):
        """High propagation but no scope questions → inconsistency."""
        coh = CoherenceEvaluation(
            coherence_score=90.0,
            verdict="pass",
            results=[
                CoherenceResult(question_id="q-test-count", question="Q1", verdict="pass",
                                value=10.0, baseline=8.0, assertion="result >= baseline"),
            ],
            harness_version="1.0.0",
        )
        risk = _risk_eval(propagation_score=45.0)

        inconsistencies = coherence.check_consistency(coh, risk)
        types = [i["type"] for i in inconsistencies]
        assert "missing_scope_validation" in types


# ---------------------------------------------------------------------------
# Phase 2 & 4: Engine integration
# ---------------------------------------------------------------------------

class TestEngineCoherenceStep:
    def test_validate_includes_coherence(self, db_path):
        """Validation pipeline includes coherence data when no harness configured."""
        intent = make_intent("coh-001")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        result = engine.validate_intent(intent, sim=sim, skip_checks=True)
        assert result["decision"] == "validated"
        # Coherence should be present (auto-pass when no config)
        assert "coherence" in result
        assert result["coherence"]["coherence_score"] == 100.0
        assert result["coherence"]["verdict"] == "pass"

    def test_validate_coherence_block(self, db_path):
        """If coherence fails, validation is blocked."""
        intent = make_intent("coh-002")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        # Mock coherence to return a failing evaluation
        fail_eval = CoherenceEvaluation(
            coherence_score=40.0,
            verdict="fail",
            results=[
                CoherenceResult(question_id="q1", question="Q1", verdict="fail",
                                value=99.0, baseline=0.0, assertion="result == 0"),
            ],
            harness_version="1.0.0",
        )
        with patch.object(coherence, "load_questions", return_value=_make_questions()), \
             patch.object(coherence, "evaluate", return_value=fail_eval):
            result = engine.validate_intent(intent, sim=sim, skip_checks=True)

        assert result["decision"] == "blocked"
        assert "coherence" in result["reason"].lower()

    def test_coherence_evaluated_event(self, db_path):
        """Coherence step emits a coherence.evaluated event when harness is configured."""
        intent = make_intent("coh-003")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        pass_eval = CoherenceEvaluation(
            coherence_score=100.0,
            verdict="pass",
            results=[
                CoherenceResult(question_id="q1", question="Q1", verdict="pass",
                                value=0.0, baseline=None, assertion="result == 0"),
            ],
            harness_version="1.0.0",
        )
        with patch.object(coherence, "load_questions", return_value=_make_questions()), \
             patch.object(coherence, "evaluate", return_value=pass_eval):
            engine.validate_intent(intent, sim=sim, skip_checks=True)

        events = event_log.query(event_type=EventType.COHERENCE_EVALUATED)
        assert len(events) == 1
        assert events[0]["payload"]["verdict"] == "pass"


# ---------------------------------------------------------------------------
# Phase 4: Review integration
# ---------------------------------------------------------------------------

class TestWarnCreatesReview:
    def test_warn_creates_review(self, db_path):
        """When coherence verdict is warn, a review task is auto-created."""
        intent = make_intent("coh-rev-001")
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")

        warn_eval = CoherenceEvaluation(
            coherence_score=70.0,
            verdict="warn",
            results=[
                CoherenceResult(question_id="q1", question="Q1", verdict="fail",
                                value=99.0, baseline=0.0, assertion="result == 0",
                                error=None),
            ],
            harness_version="1.0.0",
        )
        with patch.object(coherence, "load_questions", return_value=_make_questions()), \
             patch.object(coherence, "evaluate", return_value=warn_eval):
            result = engine.validate_intent(intent, sim=sim, skip_checks=True)

        # Should have created a review task
        review_events = event_log.query(event_type=EventType.REVIEW_REQUESTED)
        assert len(review_events) >= 1
        # The review should be triggered by "coherence"
        assert any(
            e["payload"].get("trigger") == "coherence"
            for e in review_events
        )


class TestReviewBlocksQueue:
    def test_review_blocks_queue(self, db_path):
        """Pending review prevents intent from advancing in queue."""
        from converge import reviews

        intent = make_intent("coh-queue-001", status=Status.VALIDATED)

        # Create a pending review for this intent
        reviews.request_review(intent.id, trigger="coherence")

        cfg = _config()
        opts = engine._QueueOpts(
            max_retries=3,
            use_last_simulation=True,
            skip_checks=True,
        )
        result = engine._process_single_intent(intent, cfg, opts)
        assert result["decision"] == "review_pending"

    def test_approved_review_allows_queue(self, db_path):
        """An approved review allows the intent to proceed."""
        from converge import reviews

        intent = make_intent("coh-queue-002", status=Status.VALIDATED)

        # Create and complete a review
        task = reviews.request_review(intent.id, trigger="coherence")
        reviews.assign_review(task.id, "reviewer-1")
        reviews.complete_review(task.id, resolution="approved")

        cfg = _config()
        opts = engine._QueueOpts(
            max_retries=3,
            use_last_simulation=True,
            skip_checks=True,
        )

        # Need a simulation for revalidation
        sim = Simulation(mergeable=True, files_changed=["a.py"], source="feature/x", target="main")
        event_log.append(Event(
            event_type=EventType.SIMULATION_COMPLETED,
            intent_id=intent.id,
            payload={
                "mergeable": True,
                "conflicts": [],
                "files_changed": ["a.py"],
                "source": "feature/x",
                "target": "main",
            },
        ))

        result = engine._process_single_intent(intent, cfg, opts)
        # Should not be blocked by review (it's completed)
        assert result["decision"] != "review_pending"

    def test_rejected_review_blocks_intent(self, db_path):
        """A rejected review blocks the intent."""
        from converge import reviews

        intent = make_intent("coh-queue-003", status=Status.VALIDATED)

        task = reviews.request_review(intent.id, trigger="coherence")
        reviews.assign_review(task.id, "reviewer-1")
        reviews.complete_review(task.id, resolution="rejected")

        cfg = _config()
        opts = engine._QueueOpts(max_retries=3, use_last_simulation=True, skip_checks=True)
        result = engine._process_single_intent(intent, cfg, opts)
        assert result["decision"] == "blocked"


# ---------------------------------------------------------------------------
# Phase 1: Baselines
# ---------------------------------------------------------------------------

class TestBaselines:
    def test_baseline_update_on_merge(self, db_path):
        """Baselines are updated via update_baselines()."""
        results = [
            CoherenceResult(question_id="q1", question="Q1", verdict="pass",
                            value=10.0, baseline=8.0, assertion="result >= baseline"),
            CoherenceResult(question_id="q2", question="Q2", verdict="pass",
                            value=0.0, baseline=None, assertion="result == 0"),
        ]
        baselines = coherence.update_baselines(results)
        assert baselines == {"q1": 10.0, "q2": 0.0}

        # Verify event was emitted
        events = event_log.query(event_type=EventType.COHERENCE_BASELINE_UPDATED)
        assert len(events) == 1
        assert events[0]["payload"]["baselines"] == {"q1": 10.0, "q2": 0.0}

    def test_load_baselines(self, db_path):
        """load_baselines() reads from the most recent event."""
        # Emit a baseline event
        event_log.append(Event(
            event_type=EventType.COHERENCE_BASELINE_UPDATED,
            payload={"baselines": {"q1": 5.0, "q2": 3.0}},
        ))

        baselines = coherence.load_baselines()
        assert baselines == {"q1": 5.0, "q2": 3.0}

    def test_load_baselines_empty(self, db_path):
        """No baseline events → empty dict."""
        baselines = coherence.load_baselines()
        assert baselines == {}


# ---------------------------------------------------------------------------
# Phase 1: Init and list CLI helpers
# ---------------------------------------------------------------------------

class TestInitAndList:
    def test_init_harness(self, tmp_path):
        """init_harness creates the config file."""
        config_path = tmp_path / "coherence_harness.json"
        result = coherence.init_harness(path=config_path)
        assert result["status"] == "created"
        assert config_path.exists()

        # Loading should work
        data = json.loads(config_path.read_text())
        assert data["version"] == "1.1.0"
        assert len(data["questions"]) == 5

    def test_init_harness_already_exists(self, tmp_path):
        """init_harness returns 'exists' if file already present."""
        config_path = tmp_path / "coherence_harness.json"
        config_path.write_text("{}")

        result = coherence.init_harness(path=config_path)
        assert result["status"] == "exists"

    def test_list_questions(self, tmp_path, db_path):
        """list_questions returns questions with baselines."""
        config = {
            "version": "1.0.0",
            "questions": [
                {
                    "id": "q1",
                    "question": "Q1?",
                    "check": "echo 5",
                    "assertion": "result >= baseline",
                    "severity": "high",
                    "category": "structural",
                },
            ],
        }
        config_path = tmp_path / "coherence_harness.json"
        config_path.write_text(json.dumps(config))

        # Set baselines
        event_log.append(Event(
            event_type=EventType.COHERENCE_BASELINE_UPDATED,
            payload={"baselines": {"q1": 5.0}},
        ))

        result = coherence.list_questions(path=config_path)
        assert result["version"] == "1.0.0"
        assert len(result["questions"]) == 1
        assert result["questions"][0]["baseline"] == 5.0


# ---------------------------------------------------------------------------
# Assertion evaluator
# ---------------------------------------------------------------------------

class TestAssertionEvaluator:
    def test_gte(self):
        assert coherence._evaluate_assertion("result >= 5", 6.0, None) is True
        assert coherence._evaluate_assertion("result >= 5", 5.0, None) is True
        assert coherence._evaluate_assertion("result >= 5", 4.0, None) is False

    def test_lte(self):
        assert coherence._evaluate_assertion("result <= 5", 4.0, None) is True
        assert coherence._evaluate_assertion("result <= 5", 6.0, None) is False

    def test_eq(self):
        assert coherence._evaluate_assertion("result == 0", 0.0, None) is True
        assert coherence._evaluate_assertion("result == 0", 1.0, None) is False

    def test_baseline_ref(self):
        assert coherence._evaluate_assertion("result >= baseline", 10.0, 8.0) is True
        assert coherence._evaluate_assertion("result >= baseline", 5.0, 8.0) is False

    def test_baseline_missing(self):
        """Missing baseline → assertion passes (no baseline to compare against)."""
        assert coherence._evaluate_assertion("result >= baseline", 5.0, None) is True

    def test_gt(self):
        assert coherence._evaluate_assertion("result > 5", 6.0, None) is True
        assert coherence._evaluate_assertion("result > 5", 5.0, None) is False

    def test_lt(self):
        assert coherence._evaluate_assertion("result < 5", 4.0, None) is True
        assert coherence._evaluate_assertion("result < 5", 5.0, None) is False


# ---------------------------------------------------------------------------
# Expanded template and enabled field
# ---------------------------------------------------------------------------

class TestExpandedTemplate:
    def test_template_has_multiple_questions(self):
        """HARNESS_TEMPLATE has >= 3 enabled questions."""
        enabled = [q for q in coherence.HARNESS_TEMPLATE["questions"] if q.get("enabled", True)]
        assert len(enabled) >= 3

    def test_enabled_field_respected(self, tmp_path, db_path):
        """Disabled questions are not loaded by load_questions()."""
        config = {
            "version": "1.1.0",
            "questions": [
                {
                    "id": "q-enabled",
                    "question": "Enabled?",
                    "check": "echo 1",
                    "assertion": "result == 1",
                    "severity": "high",
                    "enabled": True,
                },
                {
                    "id": "q-disabled",
                    "question": "Disabled?",
                    "check": "echo 1",
                    "assertion": "result == 1",
                    "severity": "medium",
                    "enabled": False,
                },
            ],
        }
        config_path = tmp_path / "coherence_harness.json"
        config_path.write_text(json.dumps(config))

        questions = coherence.load_questions(path=config_path)
        ids = [q.id for q in questions]
        assert "q-enabled" in ids
        assert "q-disabled" not in ids

    def test_enabled_missing_defaults_to_true(self, tmp_path, db_path):
        """Questions without 'enabled' field default to True (backward compat)."""
        config = {
            "version": "1.0.0",
            "questions": [
                {
                    "id": "q-legacy",
                    "question": "Legacy?",
                    "check": "echo 1",
                    "assertion": "result == 1",
                    "severity": "high",
                    # no 'enabled' field
                },
            ],
        }
        config_path = tmp_path / "coherence_harness.json"
        config_path.write_text(json.dumps(config))

        questions = coherence.load_questions(path=config_path)
        assert len(questions) == 1
        assert questions[0].id == "q-legacy"


# ---------------------------------------------------------------------------
# Compound assertions (AND / OR)
# ---------------------------------------------------------------------------

class TestCompoundAssertions:
    def test_and_both_true(self):
        assert coherence._evaluate_assertion("result >= 0 AND result <= 100", 50.0, None) is True

    def test_and_first_false(self):
        assert coherence._evaluate_assertion("result >= 0 AND result <= 100", -1.0, None) is False

    def test_and_second_false(self):
        assert coherence._evaluate_assertion("result >= 0 AND result <= 100", 101.0, None) is False

    def test_or_first_true(self):
        assert coherence._evaluate_assertion("result == 0 OR result >= 10", 0.0, None) is True

    def test_or_second_true(self):
        assert coherence._evaluate_assertion("result == 0 OR result >= 10", 15.0, None) is True

    def test_or_both_false(self):
        assert coherence._evaluate_assertion("result == 0 OR result >= 10", 5.0, None) is False

    def test_and_with_baseline(self):
        assert coherence._evaluate_assertion("result >= baseline AND result <= 100", 50.0, 40.0) is True
        assert coherence._evaluate_assertion("result >= baseline AND result <= 100", 30.0, 40.0) is False

    def test_case_insensitive(self):
        assert coherence._evaluate_assertion("result >= 0 and result <= 100", 50.0, None) is True
        assert coherence._evaluate_assertion("result == 0 or result >= 10", 0.0, None) is True
