"""Tests for pre-PR evaluation harness (AR-46)."""

from converge import event_log, harness
from converge.models import EventType


class TestHarnessConfig:
    def test_defaults(self, db_path):
        cfg = harness.HarnessConfig()
        assert cfg.similarity_threshold == 0.80
        assert cfg.mode == "shadow"
        assert "semantic_similarity" in cfg.rules

    def test_custom_config(self, db_path):
        cfg = harness.HarnessConfig(
            similarity_threshold=0.90,
            mode="enforce",
            rules=["description_quality"],
        )
        assert cfg.mode == "enforce"
        assert "description_quality" in cfg.rules


class TestEvaluationResult:
    def test_to_dict(self, db_path):
        result = harness.EvaluationResult(
            score=0.85, passed=True,
            signals={"max_similarity": 0.3},
            recommendations=["Add description"],
        )
        d = result.to_dict()
        assert d["score"] == 0.85
        assert d["passed"] is True


class TestDescriptionQuality:
    def test_good_description(self, db_path):
        intent_data = {
            "source": "feature/x",
            "target": "main",
            "semantic": {
                "description": "Add user authentication with OAuth2",
                "scope": ["auth", "api"],
            },
        }
        result = harness._check_description_quality(intent_data)
        assert result["score"] == 1.0
        assert result["suggestions"] == []

    def test_missing_description(self, db_path):
        intent_data = {"source": "feature/x", "target": "main", "semantic": {}}
        result = harness._check_description_quality(intent_data)
        assert result["score"] < 1.0
        assert len(result["suggestions"]) > 0

    def test_missing_source_target(self, db_path):
        intent_data = {"semantic": {"description": "something useful here"}}
        result = harness._check_description_quality(intent_data)
        assert result["score"] < 1.0


class TestEvaluateIntent:
    def test_evaluate_basic(self, db_path):
        intent_data = {
            "source": "feature/x",
            "target": "main",
            "semantic": {"description": "Add new feature"},
        }
        result = harness.evaluate_intent(intent_data)
        assert isinstance(result.score, float)
        assert result.mode == "shadow"
        assert result.passed is True  # shadow mode always passes

    def test_evaluate_emits_event(self, db_path):
        intent_data = {"source": "feature/x", "target": "main", "semantic": {}}
        harness.evaluate_intent(intent_data)
        events = event_log.query(event_type=EventType.INTENT_PRE_EVALUATED)
        assert len(events) == 1

    def test_enforce_mode_can_block(self, db_path):
        intent_data = {"semantic": {}}  # missing everything
        cfg = harness.HarnessConfig(
            mode="enforce",
            rules=["description_quality"],
        )
        result = harness.evaluate_intent(intent_data, config=cfg)
        assert result.mode == "enforce"
        assert result.score < 0.5
        assert result.passed is False

    def test_shadow_mode_always_passes(self, db_path):
        intent_data = {"semantic": {}}
        cfg = harness.HarnessConfig(mode="shadow", rules=["description_quality"])
        result = harness.evaluate_intent(intent_data, config=cfg)
        assert result.passed is True

    def test_signals_populated(self, db_path):
        intent_data = {
            "source": "feature/x",
            "target": "main",
            "semantic": {"description": "Add user auth"},
        }
        result = harness.evaluate_intent(intent_data)
        assert "description_quality" in result.signals


class TestHarnessCLIWiring:
    def test_harness_dispatch(self, db_path):
        from converge.cli import _DISPATCH
        assert ("harness", "evaluate") in _DISPATCH

    def test_harness_subcmd_attr(self, db_path):
        from converge.cli import _SUBCMD_ATTR
        assert _SUBCMD_ATTR["harness"] == "harness_cmd"
