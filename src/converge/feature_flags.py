"""Centralized feature flag registry (AR-23).

All phase capabilities can be toggled via flags. Flags default to safe/enabled
state for backward compatibility. Override via environment variables
(CONVERGE_FF_<FLAG_NAME>) or the .converge/flags.json config file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from converge import event_log
from converge.event_types import EventType
from converge.models import Event

# ---------------------------------------------------------------------------
# Flag definitions with safe defaults
# ---------------------------------------------------------------------------

_FLAG_DEFAULTS: dict[str, dict[str, Any]] = {
    # Phase 1: Commit links
    "intent_links": {"enabled": True, "description": "Track commit→intent links"},
    # Phase 2: Archaeology
    "archaeology_enhanced": {"enabled": True, "description": "Enhanced git history analysis"},
    # Phase 3: Semantics
    "intent_semantics": {"enabled": True, "description": "Semantic embeddings and similarity"},
    # Phase 4: Origin-aware policy
    "origin_policy": {"enabled": True, "description": "Origin-type policy overrides"},
    # Phase 5: Verification debt
    "verification_debt": {"enabled": True, "description": "Verification debt tracking"},
    # Phase 6: Review tasks
    "review_tasks": {"enabled": True, "description": "Human review task workflow"},
    # Phase 7: Security adapters
    "security_adapters": {"enabled": True, "description": "Security scanner integration"},
    # Phase 8: Intake control
    "intake_control": {"enabled": True, "description": "Adaptive intake throttling"},
    # Phase 5b: Semantic conflicts
    "semantic_conflicts": {"enabled": True, "mode": "shadow", "description": "Semantic conflict detection"},
    # Phase 9: Plan coordination
    "plan_coordination": {"enabled": True, "description": "Plan-based dependency enforcement"},
    # Phase 9: Audit chain
    "audit_chain": {"enabled": True, "description": "Event tamper-evidence chain"},
    # Phase 9: Code ownership
    "code_ownership": {"enabled": False, "description": "Code-area ownership SoD enforcement"},
    # Phase 9: Pre-eval harness
    "pre_eval_harness": {"enabled": True, "mode": "shadow", "description": "Pre-PR evaluation harness"},
}


@dataclass
class FlagState:
    name: str
    enabled: bool
    mode: str = ""                  # shadow | enforce (for gradual rollout)
    description: str = ""
    source: str = "default"         # default | env | config | api

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "enabled": self.enabled,
            "description": self.description,
            "source": self.source,
        }
        if self.mode:
            d["mode"] = self.mode
        return d


# ---------------------------------------------------------------------------
# Global flag cache (loaded once, refreshable)
# ---------------------------------------------------------------------------

_flags: dict[str, FlagState] = {}
_loaded = False


def _load_flags() -> None:
    """Load flags from defaults → config file → env vars (highest priority)."""
    global _flags, _loaded

    # 1. Start with defaults
    for name, cfg in _FLAG_DEFAULTS.items():
        _flags[name] = FlagState(
            name=name,
            enabled=cfg.get("enabled", True),
            mode=cfg.get("mode", ""),
            description=cfg.get("description", ""),
            source="default",
        )

    # 2. Override from config file
    for p in [Path(".converge/flags.json"), Path("flags.json")]:
        if p.exists():
            try:
                with open(p) as f:
                    data = json.load(f)
                for name, cfg in data.items():
                    if name in _flags:
                        if isinstance(cfg, bool):
                            _flags[name].enabled = cfg
                        elif isinstance(cfg, dict):
                            _flags[name].enabled = cfg.get("enabled", _flags[name].enabled)
                            _flags[name].mode = cfg.get("mode", _flags[name].mode)
                        _flags[name].source = "config"
            except (json.JSONDecodeError, IOError):
                pass
            break

    # 3. Override from environment (highest priority)
    for name in _FLAG_DEFAULTS:
        env_key = f"CONVERGE_FF_{name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            _flags[name].enabled = env_val.lower() in ("1", "true", "yes", "on")
            _flags[name].source = "env"
        mode_key = f"CONVERGE_FF_{name.upper()}_MODE"
        mode_val = os.environ.get(mode_key)
        if mode_val is not None:
            _flags[name].mode = mode_val

    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load_flags()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_enabled(flag_name: str) -> bool:
    """Check if a feature flag is enabled."""
    _ensure_loaded()
    state = _flags.get(flag_name)
    return state.enabled if state else True  # unknown flags default to enabled


def get_mode(flag_name: str) -> str:
    """Get the mode for a flag (e.g. 'shadow' or 'enforce')."""
    _ensure_loaded()
    state = _flags.get(flag_name)
    return state.mode if state else ""


def get_flag(flag_name: str) -> FlagState | None:
    """Get full flag state."""
    _ensure_loaded()
    return _flags.get(flag_name)


def list_flags() -> list[dict[str, Any]]:
    """List all flags with their current state."""
    _ensure_loaded()
    return [f.to_dict() for f in sorted(_flags.values(), key=lambda f: f.name)]


def set_flag(
    flag_name: str,
    *,
    enabled: bool | None = None,
    mode: str | None = None,
) -> FlagState | None:
    """Set a flag's state at runtime. Optionally emit an event."""
    _ensure_loaded()
    state = _flags.get(flag_name)
    if state is None:
        return None

    if enabled is not None:
        state.enabled = enabled
    if mode is not None:
        state.mode = mode
    state.source = "api"

    event_log.append(Event(
        event_type=EventType.FEATURE_FLAG_CHANGED,
        payload={
            "flag": flag_name,
            "enabled": state.enabled,
            "mode": state.mode,
        },
    ))

    return state


def reload_flags() -> None:
    """Force reload flags from all sources."""
    global _loaded
    _loaded = False
    _ensure_loaded()
