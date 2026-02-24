"""Abstract base class capturing SQL dialect differences between backends.

Subclasses implement 6 abstract members: ``_connection``, ``_ph``,
``_excluded_prefix``, ``_integrity_error``, ``_insert_or_ignore_sql``,
and ``close``.  Concrete helpers that are purely dialect-aware also live
here so that mixin classes can call them via MRO.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from converge.models import Intent, RiskLevel, Status, now_iso


class _StoreDialect(ABC):
    """Abstract SQL-dialect base.

    Provides 6 abstract members that vary per backend, plus 5 concrete
    helpers used by the mixin classes.
    """

    # ------------------------------------------------------------------
    # Abstract template methods (what varies per backend)
    # ------------------------------------------------------------------

    @abstractmethod
    def _connection(self):
        """Context manager yielding an open database connection.

        Subclasses should decorate with ``@contextmanager`` and yield a
        connection that supports ``.execute()``, ``.commit()``,
        ``.rollback()``, and cursor ``.fetchone()``/``.fetchall()``.
        """

    @property
    @abstractmethod
    def _ph(self) -> str:
        """SQL parameter placeholder: ``'?'`` for SQLite, ``'%s'`` for PostgreSQL."""

    @property
    @abstractmethod
    def _excluded_prefix(self) -> str:
        """Upsert EXCLUDED reference: ``'excluded'`` or ``'EXCLUDED'``."""

    @property
    @abstractmethod
    def _integrity_error(self) -> type[Exception]:
        """Exception type for unique constraint violations."""

    @abstractmethod
    def _insert_or_ignore_sql(
        self, table: str, columns: list[str], ph_str: str,
    ) -> str:
        """Build INSERT-or-ignore SQL for the backend dialect."""

    @abstractmethod
    def close(self) -> None: ...

    # ------------------------------------------------------------------
    # Concrete helpers
    # ------------------------------------------------------------------

    def _placeholders(self, n: int) -> str:
        """Return *n* comma-separated parameter placeholders."""
        return ", ".join([self._ph] * n)

    def _build_where(
        self, filters: dict[str, object],
    ) -> tuple[str, list]:
        """Build a WHERE clause from a {column: value} dict.

        Skips entries where value is None.  Returns (clause_str, params_list).
        clause_str is empty string when no filters match.
        """
        ph = self._ph
        clauses: list[str] = []
        params: list = []
        for col, val in filters.items():
            if val is not None:
                clauses.append(f"{col} = {ph}")
                params.append(val)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def _upsert_policy(
        self, table: str, pk_cols: dict[str, object], data: dict,
    ) -> None:
        """Generic upsert for policy tables that store JSON data blobs.

        ``pk_cols`` maps column name -> value for the primary key columns.
        ``data`` is the JSON blob to store in the ``data`` column.

        Handles the common INSERT ... ON CONFLICT pattern for tenant-scoped
        policy tables (agent_policies, risk_policies, compliance_thresholds).
        """
        import json as _json
        ph = self._ph
        ex = self._excluded_prefix
        cols = list(pk_cols.keys()) + ["data", "updated_at"]
        vals = list(pk_cols.values()) + [_json.dumps(data), now_iso()]
        conflict_cols = ", ".join(pk_cols.keys())
        update_parts = [f"data={ex}.data", f"updated_at={ex}.updated_at"]
        placeholders = ", ".join([ph] * len(cols))
        col_str = ", ".join(cols)
        update_str = ", ".join(update_parts)
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_cols}) DO UPDATE SET {update_str}",
                tuple(vals),
            )
            conn.commit()

    @staticmethod
    def _row_to_event_dict(row: Any) -> dict[str, Any]:
        """Convert a database row to an event dictionary."""
        d = dict(row)
        payload = d["payload"]
        d["payload"] = json.loads(payload) if isinstance(payload, str) else payload
        evidence = d.get("evidence") or "{}"
        d["evidence"] = json.loads(evidence) if isinstance(evidence, str) else evidence
        return d

    @staticmethod
    def _row_to_intent(row: Any) -> Intent:
        """Convert a database row to an ``Intent`` model."""
        d = dict(row)
        _json = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731
        return Intent(
            id=d["id"],
            source=d["source"],
            target=d["target"],
            status=Status(d["status"]),
            created_at=d["created_at"],
            created_by=d["created_by"],
            risk_level=RiskLevel(d["risk_level"]) if d["risk_level"] else RiskLevel.MEDIUM,
            priority=d["priority"],
            semantic=_json(d["semantic"]),
            technical=_json(d["technical"]),
            checks_required=_json(d["checks_required"]),
            dependencies=_json(d["dependencies"]),
            retries=d["retries"],
            tenant_id=d.get("tenant_id"),
            plan_id=d.get("plan_id"),
            origin_type=d.get("origin_type", "human"),
        )
