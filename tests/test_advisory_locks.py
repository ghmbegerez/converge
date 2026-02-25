"""Tests for PostgreSQL advisory locks (Initiative 3)."""
from contextlib import contextmanager
from unittest.mock import MagicMock

from converge.adapters._advisory_lock_mixin import AdvisoryLockMixin, _lock_id


def _make_mixin(fetchone_return):
    """Create an AdvisoryLockMixin with a mock _connection."""
    mixin = AdvisoryLockMixin()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = fetchone_return

    @contextmanager
    def fake_connection():
        yield mock_conn

    mixin._connection = fake_connection
    mixin._mock_conn = mock_conn  # stash for assertions
    return mixin


def test_lock_id_deterministic():
    """Same lock name always produces the same bigint."""
    assert _lock_id("queue") == _lock_id("queue")
    assert _lock_id("test-lock") == _lock_id("test-lock")


def test_lock_id_different_names():
    """Different lock names produce different IDs."""
    assert _lock_id("queue") != _lock_id("other")
    assert _lock_id("a") != _lock_id("b")


def test_acquire_release_cycle():
    """Mock pg_try_advisory_lock returns True, unlock returns True."""
    mixin = _make_mixin((True,))
    assert mixin.acquire_queue_lock_advisory("queue") is True
    assert mixin.release_queue_lock_advisory("queue") is True


def test_acquire_already_held():
    """When lock is already held, pg_try_advisory_lock returns False."""
    mixin = _make_mixin((False,))
    assert mixin.acquire_queue_lock_advisory("queue") is False


def test_force_release():
    """force_release calls pg_advisory_unlock_all."""
    mixin = _make_mixin(None)
    result = mixin.force_release_queue_lock_advisory("queue")
    assert result is True
    mixin._mock_conn.execute.assert_called_once()


def test_get_lock_info_none():
    """When no advisory lock is held, returns None."""
    mixin = _make_mixin(None)
    result = mixin.get_queue_lock_info_advisory("queue")
    assert result is None


def test_feature_flag_shadow(monkeypatch):
    """advisory_locks flag defaults to disabled + shadow mode."""
    from converge import feature_flags

    feature_flags.reload_flags()

    flag = feature_flags.get_flag("advisory_locks")
    assert flag is not None
    assert flag.enabled is False
    assert flag.mode == "shadow"


def test_sqlite_unaffected(db_path):
    """SQLite adapter is unaffected by advisory locks flag."""
    from converge import event_log

    result = event_log.acquire_queue_lock()
    assert result is True
    event_log.release_queue_lock()
