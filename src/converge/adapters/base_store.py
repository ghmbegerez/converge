"""Base class for ConvergeStore backends (template method pattern).

All shared SQL logic and business methods live here.  Backend-specific
concerns (connection management, SQL dialect placeholders, commit semantics)
are handled by a small set of abstract methods that subclasses implement.

Application code should depend on the ports, not on this module directly.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

from converge.models import Event, Intent, RiskLevel, Status, now_iso


# ---------------------------------------------------------------------------
# Schema (shared between all backends)
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    intent_id   TEXT,
    agent_id    TEXT,
    tenant_id   TEXT,
    payload     TEXT NOT NULL,
    evidence    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_type     ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_intent   ON events(intent_id);
CREATE INDEX IF NOT EXISTS idx_events_tenant   ON events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_events_time     ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_agent    ON events(agent_id);

CREATE TABLE IF NOT EXISTS intents (
    id             TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    target         TEXT NOT NULL,
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL DEFAULT 'system',
    risk_level     TEXT NOT NULL DEFAULT 'medium',
    priority       INTEGER NOT NULL DEFAULT 3,
    semantic       TEXT NOT NULL DEFAULT '{}',
    technical      TEXT NOT NULL DEFAULT '{}',
    checks_required TEXT NOT NULL DEFAULT '[]',
    dependencies   TEXT NOT NULL DEFAULT '[]',
    retries        INTEGER NOT NULL DEFAULT 0,
    tenant_id      TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status);
CREATE INDEX IF NOT EXISTS idx_intents_tenant ON intents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_intents_status_source ON intents(status, source);

CREATE TABLE IF NOT EXISTS agent_policies (
    agent_id   TEXT NOT NULL,
    tenant_id  TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS compliance_thresholds (
    tenant_id  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_policies (
    tenant_id  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queue_locks (
    lock_name   TEXT PRIMARY KEY,
    holder_pid  INTEGER NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
"""

_ALLOWED_FILTER_COLS = {"event_type", "intent_id", "agent_id", "tenant_id", "trace_id"}


# ---------------------------------------------------------------------------
# BaseConvergeStore
# ---------------------------------------------------------------------------

class BaseConvergeStore(ABC):
    """Abstract base for ConvergeStore backends using template method pattern.

    Subclasses must implement 6 abstract members that capture the differences
    between SQL backends (connection lifecycle, placeholder syntax, upsert
    keyword, constraint-error type, insert-or-ignore syntax, cleanup).

    All 23 public business methods (ports) are implemented once here.
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
        )

    # ------------------------------------------------------------------
    # EventStorePort
    # ------------------------------------------------------------------

    def append(self, event: Event) -> Event:
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO events (id, trace_id, timestamp, event_type, intent_id, "
                f"agent_id, tenant_id, payload, evidence) "
                f"VALUES ({self._placeholders(9)})",
                (
                    event.id,
                    event.trace_id,
                    event.timestamp,
                    event.event_type,
                    event.intent_id,
                    event.agent_id,
                    event.tenant_id,
                    json.dumps(event.payload),
                    json.dumps(event.evidence),
                ),
            )
            conn.commit()
        return event

    def query(
        self,
        *,
        event_type: str | None = None,
        intent_id: str | None = None,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        ph = self._ph
        clauses: list[str] = []
        params: list[Any] = []
        if event_type:
            clauses.append(f"event_type = {ph}")
            params.append(event_type)
        if intent_id:
            clauses.append(f"intent_id = {ph}")
            params.append(intent_id)
        if agent_id:
            clauses.append(f"agent_id = {ph}")
            params.append(agent_id)
        if tenant_id:
            clauses.append(f"tenant_id = {ph}")
            params.append(tenant_id)
        if since:
            clauses.append(f"timestamp >= {ph}")
            params.append(since)
        if until:
            clauses.append(f"timestamp <= {ph}")
            params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        sql = f"SELECT * FROM events{where} ORDER BY timestamp DESC LIMIT {ph}"
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_event_dict(r) for r in rows]

    def count(self, **filters: Any) -> int:
        ph = self._ph
        clauses: list[str] = []
        params: list[Any] = []
        for k, v in filters.items():
            if v is not None:
                if k not in _ALLOWED_FILTER_COLS:
                    raise ValueError(f"Invalid filter column: {k}")
                clauses.append(f"{k} = {ph}")
                params.append(v)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT COUNT(*) AS cnt FROM events{where}"
        with self._connection() as conn:
            return conn.execute(sql, params).fetchone()["cnt"]

    def prune_events(
        self,
        before: str,
        tenant_id: str | None = None,
        dry_run: bool = False,
    ) -> int:
        ph = self._ph
        clauses = [f"timestamp < {ph}"]
        params: list[Any] = [before]
        if tenant_id:
            clauses.append(f"tenant_id = {ph}")
            params.append(tenant_id)
        where = " WHERE " + " AND ".join(clauses)
        with self._connection() as conn:
            count_val = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM events{where}", params,
            ).fetchone()["cnt"]
            if not dry_run:
                conn.execute(f"DELETE FROM events{where}", params)
            conn.commit()
        return count_val

    # ------------------------------------------------------------------
    # IntentStorePort
    # ------------------------------------------------------------------

    def upsert_intent(self, intent: Intent) -> None:
        ph = self._ph
        ex = self._excluded_prefix
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO intents (id, source, target, status, created_at, created_by, "
                f"risk_level, priority, semantic, technical, checks_required, dependencies, "
                f"retries, tenant_id, updated_at) "
                f"VALUES ({self._placeholders(15)}) "
                f"ON CONFLICT(id) DO UPDATE SET "
                f"source={ex}.source, target={ex}.target, status={ex}.status, "
                f"risk_level={ex}.risk_level, priority={ex}.priority, "
                f"semantic={ex}.semantic, technical={ex}.technical, "
                f"checks_required={ex}.checks_required, "
                f"dependencies={ex}.dependencies, retries={ex}.retries, "
                f"tenant_id={ex}.tenant_id, updated_at={ex}.updated_at",
                (
                    intent.id, intent.source, intent.target, intent.status.value,
                    intent.created_at, intent.created_by, intent.risk_level.value,
                    intent.priority, json.dumps(intent.semantic),
                    json.dumps(intent.technical),
                    json.dumps(intent.checks_required),
                    json.dumps(intent.dependencies),
                    intent.retries, intent.tenant_id, now_iso(),
                ),
            )
            conn.commit()

    def get_intent(self, intent_id: str) -> Intent | None:
        ph = self._ph
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT * FROM intents WHERE id = {ph}", (intent_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_intent(row)

    def list_intents(
        self,
        *,
        status: str | None = None,
        tenant_id: str | None = None,
        source: str | None = None,
        limit: int = 200,
    ) -> list[Intent]:
        ph = self._ph
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append(f"status = {ph}")
            params.append(status)
        if tenant_id:
            clauses.append(f"tenant_id = {ph}")
            params.append(tenant_id)
        if source:
            clauses.append(f"source = {ph}")
            params.append(source)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM intents{where} ORDER BY priority ASC, created_at ASC LIMIT {ph}",
                params,
            ).fetchall()
        return [self._row_to_intent(r) for r in rows]

    def update_intent_status(
        self,
        intent_id: str,
        status: Status,
        retries: int | None = None,
    ) -> None:
        ph = self._ph
        with self._connection() as conn:
            if retries is not None:
                conn.execute(
                    f"UPDATE intents SET status = {ph}, retries = {ph}, "
                    f"updated_at = {ph} WHERE id = {ph}",
                    (status.value, retries, now_iso(), intent_id),
                )
            else:
                conn.execute(
                    f"UPDATE intents SET status = {ph}, updated_at = {ph} WHERE id = {ph}",
                    (status.value, now_iso(), intent_id),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # PolicyStorePort
    # ------------------------------------------------------------------

    def upsert_agent_policy(self, data: dict[str, Any]) -> None:
        ph = self._ph
        ex = self._excluded_prefix
        agent_id = data["agent_id"]
        tenant_id = data.get("tenant_id")
        tid = tenant_id or ""
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO agent_policies (agent_id, tenant_id, data, updated_at) "
                f"VALUES ({self._placeholders(4)}) "
                f"ON CONFLICT(agent_id, tenant_id) DO UPDATE SET "
                f"data={ex}.data, updated_at={ex}.updated_at",
                (agent_id, tid, json.dumps(data), now_iso()),
            )
            conn.commit()

    def get_agent_policy(
        self, agent_id: str, tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        ph = self._ph
        tid = tenant_id or ""
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT data FROM agent_policies WHERE agent_id = {ph} AND tenant_id = {ph}",
                (agent_id, tid),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    def list_agent_policies(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        ph = self._ph
        with self._connection() as conn:
            if tenant_id:
                rows = conn.execute(
                    f"SELECT data FROM agent_policies WHERE tenant_id = {ph} ORDER BY agent_id",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT data FROM agent_policies ORDER BY agent_id",
                ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def upsert_risk_policy(
        self, tenant_id: str, data: dict[str, Any],
    ) -> None:
        ph = self._ph
        ex = self._excluded_prefix
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT version FROM risk_policies WHERE tenant_id = {ph}",
                (tenant_id,),
            ).fetchone()
            version = (row["version"] + 1) if row else 1
            conn.execute(
                f"INSERT INTO risk_policies (tenant_id, data, version, updated_at) "
                f"VALUES ({self._placeholders(4)}) "
                f"ON CONFLICT(tenant_id) DO UPDATE SET "
                f"data={ex}.data, version={ex}.version, "
                f"updated_at={ex}.updated_at",
                (tenant_id, json.dumps(data), version, now_iso()),
            )
            conn.commit()

    def get_risk_policy(self, tenant_id: str) -> dict[str, Any] | None:
        ph = self._ph
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT data, version FROM risk_policies WHERE tenant_id = {ph}",
                (tenant_id,),
            ).fetchone()
        if row is None:
            return None
        d = json.loads(row["data"])
        d["version"] = row["version"]
        return d

    def list_risk_policies(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        ph = self._ph
        with self._connection() as conn:
            if tenant_id:
                rows = conn.execute(
                    f"SELECT tenant_id, data, version FROM risk_policies "
                    f"WHERE tenant_id = {ph} ORDER BY tenant_id",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT tenant_id, data, version FROM risk_policies ORDER BY tenant_id",
                ).fetchall()
        result = []
        for r in rows:
            d = json.loads(r["data"])
            d["tenant_id"] = r["tenant_id"]
            d["version"] = r["version"]
            result.append(d)
        return result

    def upsert_compliance_thresholds(
        self, tenant_id: str, data: dict[str, Any],
    ) -> None:
        ph = self._ph
        ex = self._excluded_prefix
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO compliance_thresholds (tenant_id, data, updated_at) "
                f"VALUES ({self._placeholders(3)}) "
                f"ON CONFLICT(tenant_id) DO UPDATE SET "
                f"data={ex}.data, updated_at={ex}.updated_at",
                (tenant_id, json.dumps(data), now_iso()),
            )
            conn.commit()

    def get_compliance_thresholds(
        self, tenant_id: str,
    ) -> dict[str, Any] | None:
        ph = self._ph
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT data FROM compliance_thresholds WHERE tenant_id = {ph}",
                (tenant_id,),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_compliance_thresholds(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        ph = self._ph
        with self._connection() as conn:
            if tenant_id:
                rows = conn.execute(
                    f"SELECT tenant_id, data FROM compliance_thresholds "
                    f"WHERE tenant_id = {ph} ORDER BY tenant_id",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT tenant_id, data FROM compliance_thresholds ORDER BY tenant_id",
                ).fetchall()
        result = []
        for r in rows:
            d = json.loads(r["data"])
            d["tenant_id"] = r["tenant_id"]
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # LockPort
    # ------------------------------------------------------------------

    def acquire_queue_lock(
        self,
        lock_name: str = "queue",
        holder_pid: int | None = None,
        ttl_seconds: int = 300,
    ) -> bool:
        ph = self._ph
        pid = holder_pid or os.getpid()
        now = now_iso()
        expires = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()
        with self._connection() as conn:
            conn.execute(
                f"DELETE FROM queue_locks WHERE lock_name = {ph} AND expires_at < {ph}",
                (lock_name, now),
            )
            try:
                conn.execute(
                    f"INSERT INTO queue_locks (lock_name, holder_pid, acquired_at, expires_at) "
                    f"VALUES ({self._placeholders(4)})",
                    (lock_name, pid, now, expires),
                )
                conn.commit()
                return True
            except self._integrity_error:
                conn.rollback()
                return False

    def release_queue_lock(
        self,
        lock_name: str = "queue",
        holder_pid: int | None = None,
    ) -> bool:
        ph = self._ph
        pid = holder_pid or os.getpid()
        with self._connection() as conn:
            cursor = conn.execute(
                f"DELETE FROM queue_locks WHERE lock_name = {ph} AND holder_pid = {ph}",
                (lock_name, pid),
            )
            conn.commit()
            return cursor.rowcount > 0

    def force_release_queue_lock(
        self, lock_name: str = "queue",
    ) -> bool:
        ph = self._ph
        with self._connection() as conn:
            cursor = conn.execute(
                f"DELETE FROM queue_locks WHERE lock_name = {ph}",
                (lock_name,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_queue_lock_info(
        self, lock_name: str = "queue",
    ) -> dict[str, Any] | None:
        ph = self._ph
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT * FROM queue_locks WHERE lock_name = {ph}",
                (lock_name,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # DeliveryPort
    # ------------------------------------------------------------------

    def is_duplicate_delivery(self, delivery_id: str) -> bool:
        ph = self._ph
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT 1 FROM webhook_deliveries WHERE delivery_id = {ph}",
                (delivery_id,),
            ).fetchone()
        return row is not None

    def record_delivery(self, delivery_id: str) -> None:
        sql = self._insert_or_ignore_sql(
            "webhook_deliveries",
            ["delivery_id", "received_at"],
            self._placeholders(2),
        )
        with self._connection() as conn:
            conn.execute(sql, (delivery_id, now_iso()))
            conn.commit()
