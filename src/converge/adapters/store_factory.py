"""Factory for creating the appropriate ConvergeStore backend.

Reads ``CONVERGE_DB_BACKEND`` (default: ``sqlite``) and returns the
corresponding store implementation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from converge.ports import ConvergeStore


def create_store(
    *,
    backend: str | None = None,
    db_path: str | Path | None = None,
    dsn: str | None = None,
    **kwargs: Any,
) -> ConvergeStore:
    """Create and return a ``ConvergeStore`` for the requested backend.

    Parameters
    ----------
    backend:
        ``"sqlite"`` or ``"postgres"``.  Falls back to the
        ``CONVERGE_DB_BACKEND`` env var (default ``"sqlite"``).
    db_path:
        Path to the SQLite file.  Required when *backend* is ``"sqlite"``.
        Falls back to ``CONVERGE_DB_PATH``.
    dsn:
        PostgreSQL connection string.  Required when *backend* is ``"postgres"``.
        Falls back to ``CONVERGE_PG_DSN``.
    **kwargs:
        Extra keyword arguments forwarded to the store constructor
        (e.g. ``min_size``, ``max_size`` for Postgres pool).
    """
    backend = (backend or os.environ.get("CONVERGE_DB_BACKEND", "sqlite")).lower()

    if backend == "sqlite":
        from converge.adapters.sqlite_store import SqliteStore

        path = db_path or os.environ.get("CONVERGE_DB_PATH", ".converge/state.db")
        return SqliteStore(path)

    if backend == "postgres":
        from converge.adapters.postgres_store import PostgresStore

        pg_dsn = dsn or os.environ.get("CONVERGE_PG_DSN")
        if not pg_dsn:
            raise ValueError(
                "PostgreSQL backend requires a DSN.  Set CONVERGE_PG_DSN or pass dsn=."
            )
        return PostgresStore(pg_dsn, **kwargs)

    raise ValueError(f"Unknown backend: {backend!r}  (expected 'sqlite' or 'postgres')")
