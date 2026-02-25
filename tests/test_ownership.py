"""Tests for code-area ownership and separation of duties (AR-45)."""

import json

from converge import event_log
from converge.models import EventType
from converge.ownership import (
    OwnershipConfig,
    OwnershipRule,
    check_sod,
    load_ownership,
    ownership_summary,
)


def _config_with_rules():
    """Create an OwnershipConfig with some rules."""
    return OwnershipConfig(
        rules=[
            OwnershipRule(pattern="src/auth/**", owners=["alice", "bob"], team="security"),
            OwnershipRule(pattern="src/api/**", owners=["charlie"], team="backend"),
            OwnershipRule(pattern="tests/**", owners=["dave"], team="testing"),
        ],
        strict=False,
    )


class TestLoadOwnership:
    def test_load_ownership_from_file(self, tmp_path):
        """Load ownership rules from a JSON file."""
        config_data = {
            "rules": [
                {"pattern": "src/auth/**", "owners": ["alice"], "team": "security"},
                {"pattern": "src/db/**", "owners": ["bob"]},
            ],
            "strict": True,
        }
        config_path = tmp_path / "ownership.json"
        config_path.write_text(json.dumps(config_data))

        cfg = load_ownership(config_path=config_path)
        assert len(cfg.rules) == 2
        assert cfg.strict is True
        assert cfg.rules[0].pattern == "src/auth/**"
        assert cfg.rules[0].owners == ["alice"]

    def test_load_ownership_no_file(self, tmp_path):
        """No config file returns empty OwnershipConfig."""
        cfg = load_ownership(config_path=tmp_path / "nonexistent.json")
        assert len(cfg.rules) == 0
        assert cfg.strict is False


class TestOwnersFor:
    def test_owners_for_matching_pattern(self):
        """Glob match returns correct owners."""
        cfg = _config_with_rules()
        owners = cfg.owners_for("src/auth/login.py")
        assert "alice" in owners
        assert "bob" in owners

    def test_owners_for_no_match(self):
        """No matching pattern returns empty list."""
        cfg = _config_with_rules()
        owners = cfg.owners_for("docs/README.md")
        assert owners == []


class TestIsOwner:
    def test_is_owner_true(self):
        """Agent that owns touched files returns True."""
        cfg = _config_with_rules()
        assert cfg.is_owner("alice", ["src/auth/login.py"]) is True

    def test_is_owner_false(self):
        """Agent that doesn't own touched files returns False."""
        cfg = _config_with_rules()
        assert cfg.is_owner("alice", ["src/api/routes.py"]) is False


class TestCheckSoD:
    def test_check_sod_violation(self, db_path):
        """Owner approving their own code is blocked."""
        cfg = _config_with_rules()
        result = check_sod(
            agent_id="alice",
            files=["src/auth/login.py"],
            action="approve",
            config=cfg,
        )
        assert result["allowed"] is False
        assert "SoD" in result["reason"]

    def test_check_sod_allowed(self, db_path):
        """Non-owner approving is allowed."""
        cfg = _config_with_rules()
        result = check_sod(
            agent_id="charlie",
            files=["src/auth/login.py"],
            action="approve",
            config=cfg,
        )
        assert result["allowed"] is True

    def test_check_sod_emits_event(self, db_path):
        """SOD_VIOLATION event is emitted on violation."""
        cfg = _config_with_rules()
        check_sod(
            agent_id="alice",
            files=["src/auth/login.py"],
            action="approve",
            config=cfg,
        )
        events = event_log.query(event_type=EventType.SOD_VIOLATION)
        assert len(events) >= 1
        assert events[0]["payload"]["agent_id"] == "alice"

    def test_check_sod_no_rules(self, db_path):
        """No ownership rules → always allowed."""
        cfg = OwnershipConfig()
        result = check_sod(
            agent_id="alice",
            files=["src/auth/login.py"],
            action="approve",
            config=cfg,
        )
        assert result["allowed"] is True

    def test_read_action_allowed_for_owner(self, db_path):
        """Non-approve actions (e.g. analyze) are allowed even for owners."""
        cfg = _config_with_rules()
        result = check_sod(
            agent_id="alice",
            files=["src/auth/login.py"],
            action="analyze",
            config=cfg,
        )
        assert result["allowed"] is True


class TestOwnershipSummary:
    def test_ownership_summary(self):
        """Owned vs unowned mapping is correct."""
        cfg = _config_with_rules()
        summary = ownership_summary(
            files=["src/auth/login.py", "src/api/routes.py", "docs/README.md"],
            config=cfg,
        )
        assert "src/auth/login.py" in summary["owned"]
        assert "src/api/routes.py" in summary["owned"]
        assert "docs/README.md" in summary["unowned"]
        assert summary["coverage"] > 0

    def test_ownership_summary_empty(self):
        """Empty file list produces zero coverage."""
        cfg = _config_with_rules()
        summary = ownership_summary(files=[], config=cfg)
        assert summary["coverage"] == 0
        assert summary["owned"] == {}
        assert summary["unowned"] == []
