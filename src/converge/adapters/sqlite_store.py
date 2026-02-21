"""SQLite implementation of ConvergeStore.

Thin subclass of ``BaseConvergeStore`` — only connection management
and SQLite-specific SQL dialect details live here.
Application code should depend on the ports, not on this module directly.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from converge.adapters.base_store import SCHEMA, BaseConvergeStore


class SqliteStore(BaseConvergeStore):
    """ConvergeStore backed by a single SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.executescript(SCHEMA)

    @property
    def db_path(self) -> Path:
        return self._db_path

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    @property
    def _ph(self) -> str:
        return "?"

    @property
    def _excluded_prefix(self) -> str:
        return "excluded"

    @property
    def _integrity_error(self) -> type[Exception]:
        return sqlite3.IntegrityError

    def _insert_or_ignore_sql(
        self, table: str, columns: list[str], ph_str: str,
    ) -> str:
        cols = ", ".join(columns)
        return f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({ph_str})"

    def close(self) -> None:
        pass  # connections are per-call; nothing to tear down
