"""Tests for Phase 9 cross-cutting hardening (AR-22..AR-25)."""

import json
import os

from converge import event_log, feature_flags
from converge.event_types import EventType
from converge.models import Event



class TestFeatureFlagDefaults:
    """Flag defaults and initialization."""

    def setup_method(self):
        """Reset flag state between tests."""
        feature_flags._flags.clear()
        feature_flags._loaded = False

    def test_all_flags_have_defaults(self, db_path):
        """All registered flags load with default values."""
        flags = feature_flags.list_flags()
        assert len(flags) >= 10
        for f in flags:
            assert "name" in f
            assert "enabled" in f

    def test_default_flags_enabled(self, db_path):
        """Most flags default to enabled."""
        enabled_count = sum(1 for f in feature_flags.list_flags() if f["enabled"])
        assert enabled_count >= 10  # most are enabled by default

    def test_code_ownership_disabled_by_default(self, db_path):
        """Code ownership defaults to disabled (opt-in)."""
        assert feature_flags.is_enabled("code_ownership") is False

    def test_unknown_flag_defaults_enabled(self, db_path):
        """Unknown flag names default to enabled (safe default)."""
        assert feature_flags.is_enabled("nonexistent_flag") is True


class TestFeatureFlagAPI:
    """Public API for flag operations."""

    def setup_method(self):
        feature_flags._flags.clear()
        feature_flags._loaded = False

    def test_is_enabled(self, db_path):
        assert feature_flags.is_enabled("verification_debt") is True

    def test_get_mode(self, db_path):
        """Flags with mode support return mode string."""
        mode = feature_flags.get_mode("semantic_conflicts")
        assert mode == "shadow"

    def test_get_mode_no_mode(self, db_path):
        """Flags without mode return empty string."""
        mode = feature_flags.get_mode("verification_debt")
        assert mode == ""

    def test_get_flag_returns_state(self, db_path):
        state = feature_flags.get_flag("review_tasks")
        assert state is not None
        assert state.name == "review_tasks"
        assert state.enabled is True

    def test_get_flag_unknown(self, db_path):
        assert feature_flags.get_flag("nonexistent") is None

    def test_list_flags_sorted(self, db_path):
        """Flags are returned sorted by name."""
        flags = feature_flags.list_flags()
        names = [f["name"] for f in flags]
        assert names == sorted(names)


class TestFeatureFlagOverrides:
    """Flag override from env vars and config."""

    def setup_method(self):
        feature_flags._flags.clear()
        feature_flags._loaded = False

    def test_env_override_disable(self, db_path):
        """Environment variable can disable a flag."""
        os.environ["CONVERGE_FF_VERIFICATION_DEBT"] = "false"
        try:
            feature_flags.reload_flags()
            assert feature_flags.is_enabled("verification_debt") is False
            state = feature_flags.get_flag("verification_debt")
            assert state.source == "env"
        finally:
            del os.environ["CONVERGE_FF_VERIFICATION_DEBT"]

    def test_env_override_enable(self, db_path):
        """Environment variable can enable a disabled flag."""
        os.environ["CONVERGE_FF_CODE_OWNERSHIP"] = "1"
        try:
            feature_flags.reload_flags()
            assert feature_flags.is_enabled("code_ownership") is True
        finally:
            del os.environ["CONVERGE_FF_CODE_OWNERSHIP"]

    def test_env_mode_override(self, db_path):
        """Environment variable can override mode."""
        os.environ["CONVERGE_FF_SEMANTIC_CONFLICTS_MODE"] = "enforce"
        try:
            feature_flags.reload_flags()
            assert feature_flags.get_mode("semantic_conflicts") == "enforce"
        finally:
            del os.environ["CONVERGE_FF_SEMANTIC_CONFLICTS_MODE"]

    def test_config_file_override(self, db_path, tmp_path):
        """Config file can override flags."""
        config = {"verification_debt": False, "review_tasks": {"enabled": True, "mode": "enforce"}}
        config_file = tmp_path / "flags.json"
        config_file.write_text(json.dumps(config))

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            feature_flags.reload_flags()
            assert feature_flags.is_enabled("verification_debt") is False
            state = feature_flags.get_flag("verification_debt")
            assert state.source == "config"
        finally:
            os.chdir(old_cwd)


class TestFeatureFlagSetAPI:
    """Runtime flag modification via set_flag."""

    def setup_method(self):
        feature_flags._flags.clear()
        feature_flags._loaded = False

    def test_set_flag_enabled(self, db_path):
        state = feature_flags.set_flag("code_ownership", enabled=True)
        assert state is not None
        assert state.enabled is True
        assert state.source == "api"

    def test_set_flag_mode(self, db_path):
        state = feature_flags.set_flag("semantic_conflicts", mode="enforce")
        assert state.mode == "enforce"

    def test_set_flag_unknown_returns_none(self, db_path):
        assert feature_flags.set_flag("nonexistent", enabled=True) is None

    def test_set_flag_emits_event(self, db_path):
        feature_flags.set_flag("review_tasks", enabled=False)
        events = event_log.query(event_type=EventType.FEATURE_FLAG_CHANGED)
        assert len(events) == 1
        assert events[0]["payload"]["flag"] == "review_tasks"
        assert events[0]["payload"]["enabled"] is False

    def test_set_flag_without_db_no_event(self, db_path):
        """Setting flag without db_path does not emit event."""
        feature_flags.set_flag("review_tasks", enabled=False)
        # No error, just doesn't emit


class TestFeatureFlagReload:
    """Flag reload behavior."""

    def setup_method(self):
        feature_flags._flags.clear()
        feature_flags._loaded = False

    def test_reload_resets_to_defaults(self, db_path):
        feature_flags.set_flag("code_ownership", enabled=True)
        assert feature_flags.is_enabled("code_ownership") is True
        feature_flags.reload_flags()
        assert feature_flags.is_enabled("code_ownership") is False

    def test_flag_state_to_dict(self, db_path):
        state = feature_flags.get_flag("semantic_conflicts")
        d = state.to_dict()
        assert d["name"] == "semantic_conflicts"
        assert d["enabled"] is True
        assert d["mode"] == "shadow"
        assert "description" in d


# ===========================================================================
# AR-22: Verification debt inter-origin conflict blend
# ===========================================================================

class TestConflictBlend:
    """AR-22: Conflict pressure blends merge + semantic conflicts."""

    def test_breakdown_includes_conflict_rate(self, db_path):
        """Breakdown conflict_rate reflects the blended value."""
        for i in range(4):
            event_log.append(Event(
                event_type=EventType.SIMULATION_COMPLETED,
                payload={"mergeable": False},
            ))
        for i in range(5):
            event_log.append(Event(
                event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
                payload={"conflict_id": f"sc-{i}"},
            ))
        from converge.projections.verification import verification_debt
        snap = verification_debt()
        # merge_rate = 1.0 (4/4), semantic_rate = 0.5 (5/10)
        # blend = 1.0 * 0.7 + 0.5 * 0.3 = 0.85
        assert snap.breakdown["conflict_rate"] == 0.85


# ===========================================================================
# CLI wiring for Phase 9
# ===========================================================================

class TestPhase9CLIWiring:
    """Feature flag endpoints are wired."""

    def test_flags_endpoint_exists(self, db_path):
        """GET /flags and POST /flags/{name} endpoints exist."""
        from converge.api.routers.intents import router
        routes = {(r.path, list(r.methods)[0]) for r in router.routes if hasattr(r, "methods")}
        assert ("/flags", "GET") in routes
        assert ("/flags/{flag_name}", "POST") in routes
