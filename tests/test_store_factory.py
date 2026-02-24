"""Tests for the store factory — always runs (SQLite backend)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from converge.adapters.store_factory import create_store
from converge.ports import ConvergeStore


class TestStoreFactorySqlite:
    def test_default_backend_is_sqlite(self, db_path, tmp_path):
        store = create_store(db_path=tmp_path / "test.db")
        assert isinstance(store, ConvergeStore)
        store.close()

    def test_explicit_sqlite_backend(self, db_path, tmp_path):
        store = create_store(backend="sqlite", db_path=tmp_path / "test.db")
        assert isinstance(store, ConvergeStore)
        assert store.count() == 0
        store.close()

    def test_sqlite_from_env(self, db_path, tmp_path):
        with patch.dict(os.environ, {
            "CONVERGE_DB_BACKEND": "sqlite",
            "CONVERGE_DB_PATH": str(tmp_path / "env.db"),
        }):
            store = create_store()
            assert isinstance(store, ConvergeStore)
            store.close()

    def test_unknown_backend_raises(self, db_path):
        with pytest.raises(ValueError, match="Unknown backend"):
            create_store(backend="mongodb")

    def test_postgres_without_dsn_raises(self, db_path):
        with patch.dict(os.environ, {}, clear=False):
            env = dict(os.environ)
            env.pop("CONVERGE_PG_DSN", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="DSN"):
                    create_store(backend="postgres")


class TestEventLogInit:
    def test_init_with_factory(self, db_path, tmp_path):
        from converge import event_log

        event_log.init(db_path=tmp_path / "factory.db")
        store = event_log.get_store()
        assert store is not None
        assert isinstance(store, ConvergeStore)

    def test_init_with_backend_kwarg(self, db_path, tmp_path):
        from converge import event_log

        event_log.init(db_path=tmp_path / "explicit.db", backend="sqlite")
        store = event_log.get_store()
        assert store is not None
