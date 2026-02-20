"""Postgres-specific tests: connection pool, migrations, store factory.

These tests only run when ``CONVERGE_TEST_PG_DSN`` is set.
Skipped gracefully otherwise.
"""

from __future__ import annotations

import os

import pytest

# Skip entire module when no Postgres DSN
pytestmark = pytest.mark.skipif(
    not os.environ.get("CONVERGE_TEST_PG_DSN"),
    reason="CONVERGE_TEST_PG_DSN not set",
)


def _dsn() -> str:
    return os.environ["CONVERGE_TEST_PG_DSN"]


def _clean_tables(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        for table in (
            "webhook_deliveries", "queue_locks", "risk_policies",
            "compliance_thresholds", "agent_policies", "intents",
            "events", "schema_migrations",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()


@pytest.fixture(autouse=True)
def _clean_pg():
    """Drop and recreate tables for each test."""
    _clean_tables(_dsn())
    yield
    _clean_tables(_dsn())


class TestPostgresPool:
    def test_pool_creates_and_closes(self):
        from converge.adapters.postgres_store import PostgresStore

        store = PostgresStore(_dsn(), min_size=1, max_size=3)
        # Pool should be usable
        assert store.count() == 0
        store.close()

    def test_pool_recovers_after_connection_return(self):
        from converge.adapters.postgres_store import PostgresStore
        from converge.models import Event

        store = PostgresStore(_dsn(), min_size=1, max_size=2)
        # Multiple operations should reuse pool connections
        for i in range(10):
            store.append(Event(
                event_type="pool.test", payload={"i": i}, trace_id=f"t-{i}",
            ))
        assert store.count() == 10
        store.close()


class TestMigrations:
    def test_up_migration_creates_tables(self):
        import psycopg

        dsn = _dsn()
        migration_path = os.path.join(
            os.path.dirname(__file__), "..", "migrations", "001_initial_up.sql",
        )
        with open(migration_path) as f:
            up_sql = f.read()

        with psycopg.connect(dsn) as conn:
            conn.execute(up_sql)
            conn.commit()

            # Verify tables exist
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name IN "
                "('events', 'intents', 'agent_policies', 'compliance_thresholds', "
                "'risk_policies', 'queue_locks', 'webhook_deliveries', 'schema_migrations')"
            ).fetchone()
            assert row[0] == 8

    def test_down_migration_drops_tables(self):
        import psycopg

        dsn = _dsn()
        up_path = os.path.join(
            os.path.dirname(__file__), "..", "migrations", "001_initial_up.sql",
        )
        down_path = os.path.join(
            os.path.dirname(__file__), "..", "migrations", "001_initial_down.sql",
        )

        with open(up_path) as f:
            up_sql = f.read()
        with open(down_path) as f:
            down_sql = f.read()

        with psycopg.connect(dsn) as conn:
            conn.execute(up_sql)
            conn.commit()
            conn.execute(down_sql)
            conn.commit()

            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name IN "
                "('events', 'intents', 'agent_policies', 'compliance_thresholds', "
                "'risk_policies', 'queue_locks', 'webhook_deliveries', 'schema_migrations')"
            ).fetchone()
            assert row[0] == 0


class TestStoreFactory:
    def test_factory_creates_postgres_store(self):
        from converge.adapters.store_factory import create_store

        store = create_store(backend="postgres", dsn=_dsn())
        assert store.count() == 0
        store.close()

    def test_factory_postgres_from_env(self):
        from unittest.mock import patch

        from converge.adapters.store_factory import create_store

        with patch.dict(os.environ, {
            "CONVERGE_DB_BACKEND": "postgres",
            "CONVERGE_PG_DSN": _dsn(),
        }):
            store = create_store()
            assert store.count() == 0
            store.close()

    def test_factory_postgres_no_dsn_raises(self):
        from unittest.mock import patch

        from converge.adapters.store_factory import create_store

        with patch.dict(os.environ, {"CONVERGE_DB_BACKEND": "postgres"}, clear=False):
            # Remove CONVERGE_PG_DSN if present
            env = dict(os.environ)
            env.pop("CONVERGE_PG_DSN", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="DSN"):
                    create_store(backend="postgres")


class TestBackfillScript:
    def test_backfill_and_verify(self, tmp_path):
        import sqlite3

        from converge.models import now_iso

        # Create and populate a SQLite DB
        sqlite_path = tmp_path / "source.db"
        from converge.adapters.sqlite_store import SqliteStore

        sq_store = SqliteStore(sqlite_path)
        from converge.models import Event, Intent, Status

        sq_store.append(Event(
            event_type="backfill.test", payload={"x": 1}, trace_id="t-bf",
        ))
        sq_store.upsert_intent(Intent(
            id="bf-i-1", source="f/a", target="main", status=Status.READY,
        ))

        # Run backfill
        from scripts.backfill_sqlite_to_pg import backfill, verify

        dsn = _dsn()
        # Ensure target tables exist
        from converge.adapters.postgres_store import PostgresStore

        pg_store = PostgresStore(dsn)
        pg_store.close()

        counts = backfill(str(sqlite_path), dsn)
        assert counts["events"] == 1
        assert counts["intents"] == 1

        assert verify(str(sqlite_path), dsn) is True
