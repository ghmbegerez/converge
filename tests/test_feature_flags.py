"""Tests for feature flags registry (AR-23)."""

import json
import os
from unittest.mock import patch

from converge import event_log, feature_flags
from converge.models import EventType


def _reset_flags():
    """Reset global flag state for clean test."""
    feature_flags._flags.clear()
    feature_flags._loaded = False


class TestDefaults:
    def test_defaults_loaded(self, db_path):
        """All flags from _FLAG_DEFAULTS are loaded."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        for name in feature_flags._FLAG_DEFAULTS:
            assert name in feature_flags._flags, f"Flag {name!r} not loaded"

    def test_is_enabled_known_flag(self, db_path):
        """Flags enabled by default return True."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        # intent_links is enabled by default
        assert feature_flags.is_enabled("intent_links") is True

    def test_is_enabled_unknown_returns_true(self, db_path):
        """Unknown flag defaults to True."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        assert feature_flags.is_enabled("nonexistent_flag_xyz") is True


class TestGetMode:
    def test_get_mode(self, db_path):
        """Flags with mode return correct string."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        # semantic_conflicts has mode="shadow" by default
        mode = feature_flags.get_mode("semantic_conflicts")
        assert mode == "shadow"

    def test_get_mode_no_mode(self, db_path):
        """Flags without mode return empty string."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        mode = feature_flags.get_mode("intent_links")
        assert mode == ""


class TestEnvOverride:
    def test_env_var_override(self, db_path):
        """Environment variable overrides default."""
        _reset_flags()
        with patch.dict(os.environ, {"CONVERGE_FF_CODE_OWNERSHIP": "1"}, clear=True):
            feature_flags._load_flags()
        assert feature_flags.is_enabled("code_ownership") is True
        assert feature_flags._flags["code_ownership"].source == "env"

    def test_env_var_disable(self, db_path):
        """Environment variable can disable a flag."""
        _reset_flags()
        with patch.dict(os.environ, {"CONVERGE_FF_INTENT_LINKS": "0"}, clear=True):
            feature_flags._load_flags()
        assert feature_flags.is_enabled("intent_links") is False

    def test_env_mode_override(self, db_path):
        """Environment variable can override mode."""
        _reset_flags()
        with patch.dict(os.environ, {"CONVERGE_FF_SEMANTIC_CONFLICTS_MODE": "enforce"}, clear=True):
            feature_flags._load_flags()
        assert feature_flags.get_mode("semantic_conflicts") == "enforce"

    def test_config_file_override(self, db_path, tmp_path, monkeypatch):
        """Config file can override flags."""
        config = {"verification_debt": False, "review_tasks": {"enabled": True, "mode": "enforce"}}
        config_file = tmp_path / "flags.json"
        config_file.write_text(json.dumps(config))

        monkeypatch.chdir(tmp_path)
        _reset_flags()
        feature_flags.reload_flags()
        assert feature_flags.is_enabled("verification_debt") is False
        state = feature_flags.get_flag("verification_debt")
        assert state.source == "config"


class TestSetFlag:
    def test_set_flag_runtime(self, db_path):
        """set_flag changes state and emits FEATURE_FLAG_CHANGED."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()

        result = feature_flags.set_flag("code_ownership", enabled=True)
        assert result is not None
        assert result.enabled is True
        assert result.source == "api"

        events = event_log.query(event_type=EventType.FEATURE_FLAG_CHANGED)
        assert len(events) >= 1
        assert events[0]["payload"]["flag"] == "code_ownership"
        assert events[0]["payload"]["enabled"] is True

    def test_set_flag_mode(self, db_path):
        """set_flag can change mode."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        state = feature_flags.set_flag("semantic_conflicts", mode="enforce")
        assert state.mode == "enforce"

    def test_set_flag_emits_event(self, db_path):
        """set_flag emits FEATURE_FLAG_CHANGED event with correct payload."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        feature_flags.set_flag("review_tasks", enabled=False)
        events = event_log.query(event_type=EventType.FEATURE_FLAG_CHANGED)
        assert len(events) == 1
        assert events[0]["payload"]["flag"] == "review_tasks"
        assert events[0]["payload"]["enabled"] is False

    def test_set_flag_unknown(self, db_path):
        """set_flag on unknown flag returns None."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        result = feature_flags.set_flag("nonexistent_xyz", enabled=True)
        assert result is None


class TestReloadAndList:
    def test_reload_resets(self, db_path):
        """reload_flags vuelve a defaults/env/config."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        feature_flags.set_flag("code_ownership", enabled=True)
        assert feature_flags.is_enabled("code_ownership") is True

        # Reload resets to defaults
        with patch.dict(os.environ, {}, clear=True):
            feature_flags.reload_flags()
        assert feature_flags.is_enabled("code_ownership") is False

    def test_list_flags(self, db_path):
        """list_flags returns all flags with required fields."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        flags = feature_flags.list_flags()
        assert len(flags) == len(feature_flags._FLAG_DEFAULTS)
        for f in flags:
            assert "name" in f
            assert "enabled" in f
            assert "description" in f

    def test_list_flags_sorted(self, db_path):
        """Flags are returned sorted by name."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        flags = feature_flags.list_flags()
        names = [f["name"] for f in flags]
        assert names == sorted(names)

    def test_flag_state_to_dict(self, db_path):
        """FlagState.to_dict() includes all fields."""
        _reset_flags()
        with patch.dict(os.environ, {}, clear=True):
            feature_flags._load_flags()
        state = feature_flags.get_flag("semantic_conflicts")
        d = state.to_dict()
        assert d["name"] == "semantic_conflicts"
        assert d["enabled"] is True
        assert d["mode"] == "shadow"
        assert "description" in d


class TestConflictBlend:
    """AR-22: Conflict pressure blends merge + semantic conflicts."""

    def test_breakdown_includes_conflict_rate(self, db_path):
        """Breakdown conflict_rate reflects the blended value."""
        from converge.models import Event
        from converge.event_types import EventType as ET
        for i in range(4):
            event_log.append(Event(
                event_type=ET.SIMULATION_COMPLETED,
                payload={"mergeable": False},
            ))
        for i in range(5):
            event_log.append(Event(
                event_type=ET.SEMANTIC_CONFLICT_DETECTED,
                payload={"conflict_id": f"sc-{i}"},
            ))
        from converge.projections.verification import verification_debt
        snap = verification_debt()
        assert snap.breakdown["conflict_rate"] == 0.85


class TestCLIWiring:
    def test_flags_endpoint_exists(self, db_path):
        """GET /flags and POST /flags/{name} endpoints exist."""
        from converge.api.routers.intents import router
        routes = {(r.path, list(r.methods)[0]) for r in router.routes if hasattr(r, "methods")}
        assert ("/flags", "GET") in routes
        assert ("/flags/{flag_name}", "POST") in routes
