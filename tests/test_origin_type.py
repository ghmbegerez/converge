"""Tests for origin_type: model, storage, ingestion, and policy by origin (AR-15..AR-17)."""

import json

from conftest import make_intent

from converge import event_log
from converge.models import Intent, RiskLevel, Status
from converge.policy import PolicyConfig, DEFAULT_PROFILES, evaluate


# ===================================================================
# AR-15: origin_type in model and storage
# ===================================================================

class TestOriginTypeModel:
    def test_default_origin_is_human(self, db_path):
        """Intent defaults to human origin."""
        intent = Intent(id="o-001", source="f/x", target="main", status=Status.READY)
        assert intent.origin_type == "human"

    def test_origin_in_to_dict(self, db_path):
        """origin_type is included in to_dict output."""
        intent = Intent(
            id="o-002", source="f/x", target="main",
            status=Status.READY, origin_type="agent",
        )
        d = intent.to_dict()
        assert d["origin_type"] == "agent"

    def test_origin_from_dict(self, db_path):
        """from_dict reads origin_type."""
        d = {"id": "o-003", "source": "f/x", "target": "main", "origin_type": "integration"}
        intent = Intent.from_dict(d)
        assert intent.origin_type == "integration"

    def test_origin_from_dict_default(self, db_path):
        """from_dict defaults to human when missing."""
        d = {"id": "o-004", "source": "f/x", "target": "main"}
        intent = Intent.from_dict(d)
        assert intent.origin_type == "human"


class TestOriginTypePersistence:
    def test_origin_persisted(self, db_path):
        """origin_type is persisted and retrieved."""
        make_intent("op-001", origin_type="agent")
        intent = event_log.get_intent("op-001")
        assert intent.origin_type == "agent"

    def test_origin_default_persisted(self, db_path):
        """Default origin is persisted correctly."""
        make_intent("op-002")
        intent = event_log.get_intent("op-002")
        assert intent.origin_type == "human"

    def test_origin_integration(self, db_path):
        """Integration origin type persisted."""
        make_intent("op-003", origin_type="integration")
        intent = event_log.get_intent("op-003")
        assert intent.origin_type == "integration"

    def test_origin_in_list(self, db_path):
        """origin_type visible when listing intents."""
        make_intent("op-004", origin_type="agent")
        make_intent("op-005", origin_type="human")
        intents = event_log.list_intents()
        origins = {i.id: i.origin_type for i in intents}
        assert origins["op-004"] == "agent"
        assert origins["op-005"] == "human"

    def test_origin_survives_upsert(self, db_path):
        """origin_type preserved through upsert."""
        make_intent("op-006", origin_type="agent")
        intent = event_log.get_intent("op-006")
        intent.priority = 1
        event_log.upsert_intent(intent)
        updated = event_log.get_intent("op-006")
        assert updated.origin_type == "agent"
        assert updated.priority == 1


# ===================================================================
# AR-17: Policy profiles by origin
# ===================================================================

class TestPolicyByOrigin:
    def test_no_overrides_returns_base_profile(self):
        """Without origin_overrides, profile_for returns base profile."""
        config = PolicyConfig(
            profiles=DEFAULT_PROFILES, queue={}, risk={},
        )
        profile = config.profile_for(RiskLevel.MEDIUM, origin_type="agent")
        assert profile == DEFAULT_PROFILES["medium"]

    def test_origin_overrides_applied(self):
        """Origin-specific overrides are merged into base profile."""
        config = PolicyConfig(
            profiles=DEFAULT_PROFILES, queue={}, risk={},
            origin_overrides={
                "agent": {
                    "medium": {"checks": ["lint", "unit_tests", "integration"]},
                },
            },
        )
        profile = config.profile_for(RiskLevel.MEDIUM, origin_type="agent")
        assert "integration" in profile["checks"]
        # Other fields from base profile are preserved
        assert "entropy_budget" in profile

    def test_origin_overrides_default_key(self):
        """_default key applies to all risk levels for an origin."""
        config = PolicyConfig(
            profiles=DEFAULT_PROFILES, queue={}, risk={},
            origin_overrides={
                "agent": {
                    "_default": {"containment_min": 0.9},
                },
            },
        )
        low = config.profile_for(RiskLevel.LOW, origin_type="agent")
        high = config.profile_for(RiskLevel.HIGH, origin_type="agent")
        assert low["containment_min"] == 0.9
        assert high["containment_min"] == 0.9

    def test_specific_override_beats_default(self):
        """Risk-level specific override takes precedence over _default."""
        config = PolicyConfig(
            profiles=DEFAULT_PROFILES, queue={}, risk={},
            origin_overrides={
                "agent": {
                    "_default": {"containment_min": 0.8},
                    "critical": {"containment_min": 0.95},
                },
            },
        )
        medium = config.profile_for(RiskLevel.MEDIUM, origin_type="agent")
        critical = config.profile_for(RiskLevel.CRITICAL, origin_type="agent")
        assert medium["containment_min"] == 0.8  # from _default
        assert critical["containment_min"] == 0.95  # from specific

    def test_human_no_overrides(self):
        """Human origin gets base profile when only agent overrides exist."""
        config = PolicyConfig(
            profiles=DEFAULT_PROFILES, queue={}, risk={},
            origin_overrides={
                "agent": {"medium": {"checks": ["lint", "unit_tests", "e2e"]}},
            },
        )
        human = config.profile_for(RiskLevel.MEDIUM, origin_type="human")
        agent = config.profile_for(RiskLevel.MEDIUM, origin_type="agent")
        assert human["checks"] == ["lint"]  # base
        assert "e2e" in agent["checks"]  # overridden

    def test_evaluate_with_origin(self):
        """evaluate() respects origin-specific stricter thresholds."""
        config = PolicyConfig(
            profiles=DEFAULT_PROFILES, queue={}, risk={},
            origin_overrides={
                "agent": {
                    "medium": {"entropy_budget": 5.0},  # much stricter
                },
            },
        )
        # Human: budget 18.0, delta 10 → ALLOW
        human_result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=10.0,
            containment_score=0.8,
            config=config,
            origin_type="human",
        )
        assert human_result.verdict.value == "ALLOW"

        # Agent: budget 5.0, delta 10 → BLOCK (entropy gate)
        agent_result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=10.0,
            containment_score=0.8,
            config=config,
            origin_type="agent",
        )
        assert agent_result.verdict.value == "BLOCK"

    def test_evaluate_no_origin_backward_compat(self):
        """evaluate() without origin_type works as before."""
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=10.0,
            containment_score=0.8,
        )
        assert result.verdict.value == "ALLOW"
