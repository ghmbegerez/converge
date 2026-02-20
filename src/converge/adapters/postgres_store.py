"""PostgreSQL implementation of ConvergeStore.

Uses psycopg 3 (sync mode) with psycopg_pool.ConnectionPool for
connection management.  Schema is identical to SQLite (TEXT columns
with JSON serialisation, not JSONB) for migration simplicity.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from converge.models import Event, Intent, RiskLevel, Status, now_iso


# ---------------------------------------------------------------------------
# Schema (Postgres dialect)
# ---------------------------------------------------------------------------

_SCHEMA = """
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
# PostgresStore
# ---------------------------------------------------------------------------

class PostgresStore:
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
        """Create tables and indexes if they don't exist."""
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    @property
    def dsn(self) -> str:
        return self._dsn

    def close(self) -> None:
        self._pool.close()

    # ------------------------------------------------------------------
    # EventStorePort
    # ------------------------------------------------------------------

    def append(self, event: Event) -> Event:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO events (id, trace_id, timestamp, event_type, intent_id, "
                "agent_id, tenant_id, payload, evidence) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
        clauses: list[str] = []
        params: list[Any] = []
        if event_type:
            clauses.append("event_type = %s")
            params.append(event_type)
        if intent_id:
            clauses.append("intent_id = %s")
            params.append(intent_id)
        if agent_id:
            clauses.append("agent_id = %s")
            params.append(agent_id)
        if tenant_id:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        if since:
            clauses.append("timestamp >= %s")
            params.append(since)
        if until:
            clauses.append("timestamp <= %s")
            params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        sql = f"SELECT * FROM events{where} ORDER BY timestamp DESC LIMIT %s"
        with self._pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_event_dict(r) for r in rows]

    def count(self, **filters: Any) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        for k, v in filters.items():
            if v is not None:
                if k not in _ALLOWED_FILTER_COLS:
                    raise ValueError(f"Invalid filter column: {k}")
                clauses.append(f"{k} = %s")
                params.append(v)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT COUNT(*) AS cnt FROM events{where}"
        with self._pool.connection() as conn:
            row = conn.execute(sql, params).fetchone()
        return row["cnt"]

    def prune_events(
        self,
        before: str,
        tenant_id: str | None = None,
        dry_run: bool = False,
    ) -> int:
        clauses = ["timestamp < %s"]
        params: list[Any] = [before]
        if tenant_id:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        where = " WHERE " + " AND ".join(clauses)
        with self._pool.connection() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS cnt FROM events{where}", params).fetchone()
            count_val = row["cnt"]
            if not dry_run:
                conn.execute(f"DELETE FROM events{where}", params)
            conn.commit()
        return count_val

    # ------------------------------------------------------------------
    # IntentStorePort
    # ------------------------------------------------------------------

    def upsert_intent(self, intent: Intent) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO intents (id, source, target, status, created_at, created_by, "
                "risk_level, priority, semantic, technical, checks_required, dependencies, "
                "retries, tenant_id, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT(id) DO UPDATE SET "
                "source=EXCLUDED.source, target=EXCLUDED.target, status=EXCLUDED.status, "
                "risk_level=EXCLUDED.risk_level, priority=EXCLUDED.priority, "
                "semantic=EXCLUDED.semantic, technical=EXCLUDED.technical, "
                "checks_required=EXCLUDED.checks_required, "
                "dependencies=EXCLUDED.dependencies, retries=EXCLUDED.retries, "
                "tenant_id=EXCLUDED.tenant_id, updated_at=EXCLUDED.updated_at",
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
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM intents WHERE id = %s", (intent_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_intent(row)

    def list_intents(
        self,
        *,
        status: str | None = None,
        tenant_id: str | None = None,
        limit: int = 200,
    ) -> list[Intent]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = %s")
            params.append(status)
        if tenant_id:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM intents{where} ORDER BY priority ASC, created_at ASC LIMIT %s",
                params,
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    def update_intent_status(
        self,
        intent_id: str,
        status: Status,
        retries: int | None = None,
    ) -> None:
        with self._pool.connection() as conn:
            if retries is not None:
                conn.execute(
                    "UPDATE intents SET status = %s, retries = %s, updated_at = %s WHERE id = %s",
                    (status.value, retries, now_iso(), intent_id),
                )
            else:
                conn.execute(
                    "UPDATE intents SET status = %s, updated_at = %s WHERE id = %s",
                    (status.value, now_iso(), intent_id),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # PolicyStorePort
    # ------------------------------------------------------------------

    def upsert_agent_policy(self, data: dict[str, Any]) -> None:
        agent_id = data["agent_id"]
        tenant_id = data.get("tenant_id")
        with self._pool.connection() as conn:
            tid = tenant_id or ""
            conn.execute(
                "INSERT INTO agent_policies (agent_id, tenant_id, data, updated_at) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT(agent_id, tenant_id) DO UPDATE SET "
                "data=EXCLUDED.data, updated_at=EXCLUDED.updated_at",
                (agent_id, tid, json.dumps(data), now_iso()),
            )
            conn.commit()

    def get_agent_policy(
        self, agent_id: str, tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        tid = tenant_id or ""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT data FROM agent_policies WHERE agent_id = %s AND tenant_id = %s",
                (agent_id, tid),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    def list_agent_policies(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            if tenant_id:
                rows = conn.execute(
                    "SELECT data FROM agent_policies WHERE tenant_id = %s ORDER BY agent_id",
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
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT version FROM risk_policies WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchone()
            version = (row["version"] + 1) if row else 1
            conn.execute(
                "INSERT INTO risk_policies (tenant_id, data, version, updated_at) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT(tenant_id) DO UPDATE SET "
                "data=EXCLUDED.data, version=EXCLUDED.version, "
                "updated_at=EXCLUDED.updated_at",
                (tenant_id, json.dumps(data), version, now_iso()),
            )
            conn.commit()

    def get_risk_policy(self, tenant_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT data, version FROM risk_policies WHERE tenant_id = %s",
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
        with self._pool.connection() as conn:
            if tenant_id:
                rows = conn.execute(
                    "SELECT tenant_id, data, version FROM risk_policies "
                    "WHERE tenant_id = %s ORDER BY tenant_id",
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
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO compliance_thresholds (tenant_id, data, updated_at) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT(tenant_id) DO UPDATE SET "
                "data=EXCLUDED.data, updated_at=EXCLUDED.updated_at",
                (tenant_id, json.dumps(data), now_iso()),
            )
            conn.commit()

    def get_compliance_thresholds(
        self, tenant_id: str,
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT data FROM compliance_thresholds WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_compliance_thresholds(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            if tenant_id:
                rows = conn.execute(
                    "SELECT tenant_id, data FROM compliance_thresholds "
                    "WHERE tenant_id = %s ORDER BY tenant_id",
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
        pid = holder_pid or os.getpid()
        now = now_iso()
        expires = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM queue_locks WHERE lock_name = %s AND expires_at < %s",
                (lock_name, now),
            )
            try:
                conn.execute(
                    "INSERT INTO queue_locks (lock_name, holder_pid, acquired_at, expires_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (lock_name, pid, now, expires),
                )
                conn.commit()
                return True
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                return False

    def release_queue_lock(
        self,
        lock_name: str = "queue",
        holder_pid: int | None = None,
    ) -> bool:
        pid = holder_pid or os.getpid()
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM queue_locks WHERE lock_name = %s AND holder_pid = %s",
                (lock_name, pid),
            )
            conn.commit()
            return cur.rowcount > 0

    def force_release_queue_lock(
        self, lock_name: str = "queue",
    ) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM queue_locks WHERE lock_name = %s",
                (lock_name,),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_queue_lock_info(
        self, lock_name: str = "queue",
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM queue_locks WHERE lock_name = %s",
                (lock_name,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # DeliveryPort
    # ------------------------------------------------------------------

    def is_duplicate_delivery(self, delivery_id: str) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM webhook_deliveries WHERE delivery_id = %s",
                (delivery_id,),
            ).fetchone()
        return row is not None

    def record_delivery(self, delivery_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO webhook_deliveries (delivery_id, received_at) "
                "VALUES (%s, %s) ON CONFLICT (delivery_id) DO NOTHING",
                (delivery_id, now_iso()),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Row helpers (module-private)
# ---------------------------------------------------------------------------

def _row_to_event_dict(row: dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    d["payload"] = json.loads(d["payload"]) if isinstance(d["payload"], str) else d["payload"]
    evidence_raw = d.get("evidence") or "{}"
    d["evidence"] = json.loads(evidence_raw) if isinstance(evidence_raw, str) else evidence_raw
    return d


def _row_to_intent(row: dict[str, Any]) -> Intent:
    d = dict(row)
    return Intent(
        id=d["id"],
        source=d["source"],
        target=d["target"],
        status=Status(d["status"]),
        created_at=d["created_at"],
        created_by=d["created_by"],
        risk_level=RiskLevel(d["risk_level"]) if d["risk_level"] else RiskLevel.MEDIUM,
        priority=d["priority"],
        semantic=json.loads(d["semantic"]) if isinstance(d["semantic"], str) else d["semantic"],
        technical=json.loads(d["technical"]) if isinstance(d["technical"], str) else d["technical"],
        checks_required=json.loads(d["checks_required"]) if isinstance(d["checks_required"], str) else d["checks_required"],
        dependencies=json.loads(d["dependencies"]) if isinstance(d["dependencies"], str) else d["dependencies"],
        retries=d["retries"],
        tenant_id=d.get("tenant_id"),
    )
