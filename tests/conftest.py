"""Shared fixtures for converge tests."""

import tempfile
from pathlib import Path

import pytest

from converge import event_log
from converge.adapters.sqlite_store import SqliteStore
from converge.models import Event, Intent, RiskLevel, Status, now_iso


@pytest.fixture(autouse=True)
def _reset_store():
    """Reset global singletons after every test."""
    yield
    event_log._store = None
    # Reset rate limiter and rotated keys
    from converge.api.rate_limit import reset_limiter
    from converge.api.auth import reset_rotated_keys
    reset_limiter()
    reset_rotated_keys()


@pytest.fixture
def db_path(tmp_path):
    """Fresh SQLite database for each test."""
    path = tmp_path / "test_state.db"
    store = SqliteStore(path)
    event_log.configure(store)
    return path


@pytest.fixture
def store(tmp_path):
    """Return a fresh SqliteStore (not wired to the event_log facade)."""
    path = tmp_path / "contract_state.db"
    return SqliteStore(path)


def make_intent(id, **kw):
    """Shared test helper: create an Intent, persist it, return it.

    Usage::

        from conftest import make_intent
        intent = make_intent("my-001", risk_level=RiskLevel.HIGH)
    """
    defaults = dict(
        source="feature/x", target="main",
        status=Status.READY, risk_level=RiskLevel.MEDIUM,
        priority=2, tenant_id="team-a",
    )
    defaults.update(kw)
    intent = Intent(id=id, **defaults)
    event_log.upsert_intent(intent)
    return intent


@pytest.fixture
def sample_intent() -> Intent:
    return Intent(
        id="test-001",
        source="feature/login",
        target="main",
        status=Status.READY,
        created_by="test",
        risk_level=RiskLevel.MEDIUM,
        priority=2,
        semantic={"problem_statement": "Add login", "objective": "User auth"},
        technical={"source_ref": "feature/login", "target_ref": "main",
                   "initial_base_commit": "abc123", "scope_hint": ["auth", "api"]},
        dependencies=["dep-001"],
        tenant_id="team-a",
    )


@pytest.fixture
def sample_intent_high_risk() -> Intent:
    return Intent(
        id="test-002",
        source="feature/refactor-core",
        target="main",
        status=Status.READY,
        created_by="test",
        risk_level=RiskLevel.HIGH,
        priority=1,
        semantic={"problem_statement": "Core refactor", "objective": "Simplify internals"},
        technical={"scope_hint": ["core", "db", "api", "auth"]},
        dependencies=["dep-001", "dep-002", "dep-003", "dep-004"],
        tenant_id="team-a",
    )
