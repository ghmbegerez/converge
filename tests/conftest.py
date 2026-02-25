"""Shared fixtures for converge tests."""

import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from converge import event_log
from converge.adapters.sqlite_store import SqliteStore
from converge.models import Event, Intent, RiskLevel, Status, now_iso


# ---------------------------------------------------------------------------
# Auto-use fixtures: cleanup global state between tests
# ---------------------------------------------------------------------------

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


@pytest.fixture(autouse=True)
def _reset_feature_flags():
    """Reset feature flag global state after every test."""
    yield
    from converge import feature_flags
    feature_flags._flags.clear()
    feature_flags._loaded = False


# ---------------------------------------------------------------------------
# Shared live_server fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def live_server(db_path):
    """Start a FastAPI/uvicorn server on a random port for testing.

    Auth and rate limiting are disabled by default.  Files that need a
    specialised configuration (e.g. rate-limit enabled) should define their
    own ``live_server`` fixture — it will shadow this one.
    """
    import uvicorn
    from converge.api import create_app

    with patch.dict(os.environ, {
        "CONVERGE_AUTH_REQUIRED": "0",
        "CONVERGE_RATE_LIMIT_ENABLED": "0",
    }):
        app = create_app(db_path=str(db_path), webhook_secret="")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)

        yield f"http://127.0.0.1:{port}"

        server.should_exit = True
        thread.join(timeout=5)


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


# ---------------------------------------------------------------------------
# Marker registration and auto-tagging
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks integration tests (live server, git repos)")


def pytest_collection_modifyitems(items):
    """Auto-mark tests that use live_server or git_repo fixtures as integration."""
    for item in items:
        if "live_server" in item.fixturenames:
            item.add_marker(pytest.mark.integration)
