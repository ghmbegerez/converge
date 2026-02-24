"""PostgreSQL implementation of ConvergeStore.

Thin subclass of ``BaseConvergeStore`` — only connection-pool management
and PostgreSQL-specific SQL dialect details live here.
Uses psycopg 3 (sync mode) with ``psycopg_pool.ConnectionPool``.
"""

from __future__ import annotations

from contextlib import contextmanager

import psycopg.errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from converge.adapters.base_store import SCHEMA, _MIGRATIONS, BaseConvergeStore


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
