"""PostgreSQL implementation of ConvergeStore.

Thin subclass of ``BaseConvergeStore`` — only connection-pool management
and PostgreSQL-specific SQL dialect details live here.
Uses psycopg 3 (sync mode) with ``psycopg_pool.ConnectionPool``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

import psycopg.errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from converge.adapters.base_store import SCHEMA, _MIGRATIONS, BaseConvergeStore

_log = logging.getLogger("converge.adapters.postgres")


class PostgresStore(BaseConvergeStore):
    """ConvergeStore backed by PostgreSQL via psycopg 3 + connection pool."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
        run_schema: bool = True,
    ) -> None:
        self._dsn = dsn
        self._pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
        )
        if run_schema:
            self._apply_schema()

    def _apply_schema(self) -> None:
        """Create tables and indexes if they don't exist, then run migrations."""
        with self._pool.connection() as conn:
            conn.execute(SCHEMA)
            for migration in _MIGRATIONS:
                try:
                    conn.execute(migration)
                except Exception:
                    pass  # column/table already exists
            conn.commit()

    @property
    def dsn(self) -> str:
        return self._dsn

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    @contextmanager
    def _connection(self):
        with self._pool.connection() as conn:
            yield conn

    @property
    def _ph(self) -> str:
        return "%s"

    @property
    def _excluded_prefix(self) -> str:
        return "EXCLUDED"

    @property
    def _integrity_error(self) -> type[Exception]:
        return psycopg.errors.UniqueViolation

    def _insert_or_ignore_sql(
        self, table: str, columns: list[str], ph_str: str,
    ) -> str:
        cols = ", ".join(columns)
        pk = columns[0]
        return f"INSERT INTO {table} ({cols}) VALUES ({ph_str}) ON CONFLICT ({pk}) DO NOTHING"

    def close(self) -> None:
        self._pool.close()

    # ------------------------------------------------------------------
    # Advisory lock overrides (Initiative 3)
    # ------------------------------------------------------------------

    def acquire_queue_lock(
        self, lock_name: str = "queue", holder_pid: int | None = None, ttl_seconds: int = 300,
    ) -> bool:
        from converge.feature_flags import get_mode, is_enabled

        if is_enabled("advisory_locks") and get_mode("advisory_locks") == "enforce":
            from converge.adapters._advisory_lock_mixin import AdvisoryLockMixin

            return AdvisoryLockMixin.acquire_queue_lock_advisory(self, lock_name, holder_pid, ttl_seconds)
        if is_enabled("advisory_locks") and get_mode("advisory_locks") == "shadow":
            from converge.adapters._advisory_lock_mixin import AdvisoryLockMixin

            table_result = super().acquire_queue_lock(lock_name, holder_pid, ttl_seconds)
            try:
                advisory_result = AdvisoryLockMixin.acquire_queue_lock_advisory(self, lock_name, holder_pid, ttl_seconds)
                if table_result != advisory_result:
                    _log.warning("Lock divergence (acquire): table=%s advisory=%s", table_result, advisory_result)
            except Exception:
                _log.debug("Advisory lock shadow acquire failed", exc_info=True)
            return table_result
        return super().acquire_queue_lock(lock_name, holder_pid, ttl_seconds)

    def release_queue_lock(
        self, lock_name: str = "queue", holder_pid: int | None = None,
    ) -> bool:
        from converge.feature_flags import get_mode, is_enabled

        if is_enabled("advisory_locks") and get_mode("advisory_locks") == "enforce":
            from converge.adapters._advisory_lock_mixin import AdvisoryLockMixin

            return AdvisoryLockMixin.release_queue_lock_advisory(self, lock_name, holder_pid)
        if is_enabled("advisory_locks") and get_mode("advisory_locks") == "shadow":
            from converge.adapters._advisory_lock_mixin import AdvisoryLockMixin

            table_result = super().release_queue_lock(lock_name, holder_pid)
            try:
                advisory_result = AdvisoryLockMixin.release_queue_lock_advisory(self, lock_name, holder_pid)
                if table_result != advisory_result:
                    _log.warning("Lock divergence (release): table=%s advisory=%s", table_result, advisory_result)
            except Exception:
                _log.debug("Advisory lock shadow release failed", exc_info=True)
            return table_result
        return super().release_queue_lock(lock_name, holder_pid)

    def force_release_queue_lock(self, lock_name: str = "queue") -> bool:
        from converge.feature_flags import get_mode, is_enabled

        if is_enabled("advisory_locks") and get_mode("advisory_locks") == "enforce":
            from converge.adapters._advisory_lock_mixin import AdvisoryLockMixin

            return AdvisoryLockMixin.force_release_queue_lock_advisory(self, lock_name)
        return super().force_release_queue_lock(lock_name)

    def get_queue_lock_info(self, lock_name: str = "queue") -> dict | None:
        from converge.feature_flags import get_mode, is_enabled

        if is_enabled("advisory_locks") and get_mode("advisory_locks") == "enforce":
            from converge.adapters._advisory_lock_mixin import AdvisoryLockMixin

            return AdvisoryLockMixin.get_queue_lock_info_advisory(self, lock_name)
        return super().get_queue_lock_info(lock_name)
