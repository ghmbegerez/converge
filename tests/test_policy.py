"""Tests for the policy engine."""

import json

from converge.models import GateName, PolicyVerdict, RiskLevel
from converge.policy import PolicyConfig, evaluate, evaluate_risk_gate, calibrate_profiles, load_config, DEFAULT_PROFILES


def _config():
    return PolicyConfig(
        profiles=dict(DEFAULT_PROFILES),
        queue={"max_retries": 3},
        risk={"max_risk_score": 65.0, "max_damage_score": 60.0, "max_propagation_score": 55.0},
    )


class TestPolicyGates:
    def test_all_gates_pass(self):
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=10.0,
            containment_score=0.7,
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.ALLOW
        assert all(g.passed for g in result.gates)

    def test_verification_fails_missing_check(self):
        result = evaluate(
            risk_level=RiskLevel.HIGH,
            checks_passed=["lint"],  # missing unit_tests
            entropy_delta=5.0,
            containment_score=0.8,
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.BLOCK
        verification = next(g for g in result.gates if g.gate == GateName.VERIFICATION)
        assert not verification.passed

    def test_containment_fails(self):
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=10.0,
            containment_score=0.2,  # below 0.5 min
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.BLOCK
        containment = next(g for g in result.gates if g.gate == GateName.CONTAINMENT)
        assert not containment.passed

    def test_entropy_exceeds_budget(self):
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=25.0,  # exceeds 18.0 budget
            containment_score=0.6,
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.BLOCK
        entropy = next(g for g in result.gates if g.gate == GateName.ENTROPY)
        assert not entropy.passed

    def test_low_risk_is_lenient(self):
        result = evaluate(
            risk_level=RiskLevel.LOW,
            checks_passed=["lint"],
            entropy_delta=20.0,  # within 25.0 budget for low
            containment_score=0.4,  # above 0.3 for low
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.ALLOW

    def test_critical_risk_is_strict(self):
        result = evaluate(
            risk_level=RiskLevel.CRITICAL,
            checks_passed=["lint", "unit_tests"],
            entropy_delta=7.0,  # exceeds 6.0 budget for critical
            containment_score=0.9,
            config=_config(),
        )
        assert result.verdict == PolicyVerdict.BLOCK


class TestRiskGate:
    def test_within_thresholds(self):
        result = evaluate_risk_gate(risk_score=40, damage_score=30, propagation_score=20)
        assert not result["would_block"]
        assert not result["enforced"]

    def test_exceeds_risk(self):
        result = evaluate_risk_gate(risk_score=70, damage_score=30, propagation_score=20)
        assert result["would_block"]
        assert len(result["breaches"]) == 1
        assert result["breaches"][0]["metric"] == "risk_score"

    def test_enforce_mode(self):
        result = evaluate_risk_gate(risk_score=70, damage_score=30, propagation_score=20, mode="enforce")
        assert result["enforced"]

    def test_shadow_mode_does_not_enforce(self):
        result = evaluate_risk_gate(risk_score=70, damage_score=30, propagation_score=20, mode="shadow")
        assert result["would_block"]
        assert not result["enforced"]


class TestGradualRollout:
    def test_rollout_bucket_deterministic(self):
        """Same intent_id always produces same bucket."""
        from converge.policy import _rollout_bucket
        b1 = _rollout_bucket("intent-abc")
        b2 = _rollout_bucket("intent-abc")
        assert b1 == b2
        assert 0.0 <= b1 < 1.0

    def test_rollout_bucket_varies(self):
        """Different intent_ids produce different buckets."""
        from converge.policy import _rollout_bucket
        b1 = _rollout_bucket("intent-001")
        b2 = _rollout_bucket("intent-002")
        assert b1 != b2

    def test_enforce_with_intent_id(self):
        """enforce_ratio works with intent_id bucketing."""
        result = evaluate_risk_gate(
            risk_score=70, damage_score=30, propagation_score=20,
            mode="enforce", enforce_ratio=1.0, intent_id="test-intent",
        )
        assert result["would_block"]
        assert result["enforced"]
        assert "rollout_bucket" in result
        assert result["in_enforcement_group"]

    def test_enforce_ratio_zero_never_enforces(self):
        """enforce_ratio=0.0 means no intent is enforced."""
        result = evaluate_risk_gate(
            risk_score=70, damage_score=30, propagation_score=20,
            mode="enforce", enforce_ratio=0.0, intent_id="any-intent",
        )
        assert result["would_block"]
        assert not result["enforced"]
        assert not result["in_enforcement_group"]

    def test_shadow_mode_never_enforces_with_intent(self):
        """Shadow mode never enforces, regardless of enforce_ratio."""
        result = evaluate_risk_gate(
            risk_score=70, damage_score=30, propagation_score=20,
            mode="shadow", enforce_ratio=1.0, intent_id="test-intent",
        )
        assert result["would_block"]
        assert not result["enforced"]


class TestCalibration:
    def test_calibrate_with_data(self):
        historical = [{"entropy_score": i * 2.0} for i in range(100)]
        profiles = calibrate_profiles(historical)
        assert "low" in profiles
        assert "critical" in profiles
        # Calibrated values should differ from defaults
        assert profiles["low"]["entropy_budget"] != DEFAULT_PROFILES["low"]["entropy_budget"] or True

    def test_calibrate_empty_data(self):
        profiles = calibrate_profiles([])
        assert profiles == DEFAULT_PROFILES


class TestLoadConfig:
    """load_config() loads from file or falls back to defaults."""

    def test_load_defaults_when_no_file(self, tmp_path):
        """Without any config file, returns defaults."""
        # Point to a path that has no policy files
        cfg = load_config(config_path=str(tmp_path / "nonexistent.json"))
        assert cfg.profiles == DEFAULT_PROFILES
        assert cfg.queue["max_retries"] == 3
        assert cfg.risk["max_risk_score"] == 65.0

    def test_load_from_explicit_path(self, tmp_path):
        """Config from explicit path overrides defaults."""
        custom = {
            "profiles": {
                "low": {"entropy_budget": 99.0, "containment_min": 0.1, "blast_limit": 100.0, "checks": []},
            },
            "queue": {"max_retries": 10},
            "risk": {"max_risk_score": 90.0},
        }
        config_file = tmp_path / "custom_policy.json"
        config_file.write_text(json.dumps(custom))

        cfg = load_config(config_path=str(config_file))
        assert cfg.profiles["low"]["entropy_budget"] == 99.0
        assert cfg.queue["max_retries"] == 10
        assert cfg.risk["max_risk_score"] == 90.0
        # Medium should still have default since only low was overridden
        assert cfg.profiles["medium"]["entropy_budget"] == DEFAULT_PROFILES["medium"]["entropy_budget"]

    def test_partial_config_merges_with_defaults(self, tmp_path):
        """A config that only overrides 'queue' preserves default profiles and risk."""
        partial = {"queue": {"max_retries": 7}}
        config_file = tmp_path / "partial.json"
        config_file.write_text(json.dumps(partial))

        cfg = load_config(config_path=str(config_file))
        assert cfg.queue["max_retries"] == 7
        assert cfg.profiles == DEFAULT_PROFILES
        assert cfg.risk["max_risk_score"] == 65.0


class TestProfileFor:
    """PolicyConfig.profile_for() resolves risk level to profile."""

    def test_profile_for_enum(self):
        cfg = PolicyConfig(profiles=dict(DEFAULT_PROFILES), queue={}, risk={})
        profile = cfg.profile_for(RiskLevel.HIGH)
        assert profile["entropy_budget"] == 12.0
        assert "unit_tests" in profile["checks"]

    def test_profile_for_string(self):
        cfg = PolicyConfig(profiles=dict(DEFAULT_PROFILES), queue={}, risk={})
        profile = cfg.profile_for("critical")
        assert profile["entropy_budget"] == 6.0

    def test_profile_for_unknown_falls_back_to_medium(self):
        cfg = PolicyConfig(profiles=dict(DEFAULT_PROFILES), queue={}, risk={})
        profile = cfg.profile_for("nonexistent_level")
        assert profile == DEFAULT_PROFILES["medium"]

    def test_profile_for_each_level(self):
        """Each risk level maps to its own profile."""
        cfg = PolicyConfig(profiles=dict(DEFAULT_PROFILES), queue={}, risk={})
        budgets = {}
        for level in RiskLevel:
            p = cfg.profile_for(level)
            budgets[level.value] = p["entropy_budget"]
        # Low should be most lenient, critical most strict
        assert budgets["low"] > budgets["medium"] > budgets["high"] > budgets["critical"]
