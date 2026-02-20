"""SQLite implementation of ConvergeStore.

All SQL, schema management, and low-level persistence lives here.
Application code should depend on the ports, not on this module directly.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from converge.models import Event, Intent, RiskLevel, Status, now_iso


# ---------------------------------------------------------------------------
# Schema
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
# SqliteStore
# ---------------------------------------------------------------------------

class SqliteStore:
    """ConvergeStore backed by a single SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.executescript(_SCHEMA)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        pass  # connections are per-call; nothing to tear down

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------
    # EventStorePort
    # ------------------------------------------------------------------

    def append(self, event: Event) -> Event:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (id, trace_id, timestamp, event_type, intent_id, "
                "agent_id, tenant_id, payload, evidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            clauses.append("event_type = ?")
            params.append(event_type)
        if intent_id:
            clauses.append("intent_id = ?")
            params.append(intent_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        sql = f"SELECT * FROM events{where} ORDER BY timestamp DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_event_dict(r) for r in rows]

    def count(self, **filters: Any) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        for k, v in filters.items():
            if v is not None:
                if k not in _ALLOWED_FILTER_COLS:
                    raise ValueError(f"Invalid filter column: {k}")
                clauses.append(f"{k} = ?")
                params.append(v)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT COUNT(*) FROM events{where}"
        with self._connect() as conn:
            return conn.execute(sql, params).fetchone()[0]

    def prune_events(
        self,
        before: str,
        tenant_id: str | None = None,
        dry_run: bool = False,
    ) -> int:
        clauses = ["timestamp < ?"]
        params: list[Any] = [before]
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        where = " WHERE " + " AND ".join(clauses)
        with self._connect() as conn:
            count_val = conn.execute(f"SELECT COUNT(*) FROM events{where}", params).fetchone()[0]
            if not dry_run:
                conn.execute(f"DELETE FROM events{where}", params)
        return count_val

    # ------------------------------------------------------------------
    # IntentStorePort
    # ------------------------------------------------------------------

    def upsert_intent(self, intent: Intent) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO intents (id, source, target, status, created_at, created_by, "
                "risk_level, priority, semantic, technical, checks_required, dependencies, "
                "retries, tenant_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "source=excluded.source, target=excluded.target, status=excluded.status, "
                "risk_level=excluded.risk_level, priority=excluded.priority, "
                "semantic=excluded.semantic, technical=excluded.technical, "
                "checks_required=excluded.checks_required, "
                "dependencies=excluded.dependencies, retries=excluded.retries, "
                "tenant_id=excluded.tenant_id, updated_at=excluded.updated_at",
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

    def get_intent(self, intent_id: str) -> Intent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM intents WHERE id = ?", (intent_id,),
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
            clauses.append("status = ?")
            params.append(status)
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM intents{where} ORDER BY priority ASC, created_at ASC LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    def update_intent_status(
        self,
        intent_id: str,
        status: Status,
        retries: int | None = None,
    ) -> None:
        with self._connect() as conn:
            if retries is not None:
                conn.execute(
                    "UPDATE intents SET status = ?, retries = ?, updated_at = ? WHERE id = ?",
                    (status.value, retries, now_iso(), intent_id),
                )
            else:
                conn.execute(
                    "UPDATE intents SET status = ?, updated_at = ? WHERE id = ?",
                    (status.value, now_iso(), intent_id),
                )

    # ------------------------------------------------------------------
    # PolicyStorePort
    # ------------------------------------------------------------------

    def upsert_agent_policy(self, data: dict[str, Any]) -> None:
        agent_id = data["agent_id"]
        tenant_id = data.get("tenant_id")
        with self._connect() as conn:
            tid = tenant_id or ""
            conn.execute(
                "INSERT INTO agent_policies (agent_id, tenant_id, data, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(agent_id, tenant_id) DO UPDATE SET "
                "data=excluded.data, updated_at=excluded.updated_at",
                (agent_id, tid, json.dumps(data), now_iso()),
            )

    def get_agent_policy(
        self, agent_id: str, tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        tid = tenant_id or ""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM agent_policies WHERE agent_id = ? AND tenant_id = ?",
                (agent_id, tid),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    def list_agent_policies(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if tenant_id:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT data FROM agent_policies WHERE tenant_id = ? ORDER BY agent_id",
                    (tenant_id,),
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT data FROM agent_policies ORDER BY agent_id",
                ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def upsert_risk_policy(
        self, tenant_id: str, data: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT version FROM risk_policies WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            version = (row["version"] + 1) if row else 1
            conn.execute(
                "INSERT INTO risk_policies (tenant_id, data, version, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET "
                "data=excluded.data, version=excluded.version, "
                "updated_at=excluded.updated_at",
                (tenant_id, json.dumps(data), version, now_iso()),
            )

    def get_risk_policy(self, tenant_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data, version FROM risk_policies WHERE tenant_id = ?",
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
        if tenant_id:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT tenant_id, data, version FROM risk_policies "
                    "WHERE tenant_id = ? ORDER BY tenant_id",
                    (tenant_id,),
                ).fetchall()
        else:
            with self._connect() as conn:
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
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO compliance_thresholds (tenant_id, data, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET "
                "data=excluded.data, updated_at=excluded.updated_at",
                (tenant_id, json.dumps(data), now_iso()),
            )

    def get_compliance_thresholds(
        self, tenant_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM compliance_thresholds WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_compliance_thresholds(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if tenant_id:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT tenant_id, data FROM compliance_thresholds "
                    "WHERE tenant_id = ? ORDER BY tenant_id",
                    (tenant_id,),
                ).fetchall()
        else:
            with self._connect() as conn:
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
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM queue_locks WHERE lock_name = ? AND expires_at < ?",
                (lock_name, now),
            )
            try:
                conn.execute(
                    "INSERT INTO queue_locks (lock_name, holder_pid, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    (lock_name, pid, now, expires),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def release_queue_lock(
        self,
        lock_name: str = "queue",
        holder_pid: int | None = None,
    ) -> bool:
        pid = holder_pid or os.getpid()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM queue_locks WHERE lock_name = ? AND holder_pid = ?",
                (lock_name, pid),
            )
            return cursor.rowcount > 0

    def force_release_queue_lock(
        self, lock_name: str = "queue",
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM queue_locks WHERE lock_name = ?",
                (lock_name,),
            )
            return cursor.rowcount > 0

    def get_queue_lock_info(
        self, lock_name: str = "queue",
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM queue_locks WHERE lock_name = ?",
                (lock_name,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # DeliveryPort
    # ------------------------------------------------------------------

    def is_duplicate_delivery(self, delivery_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM webhook_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        return row is not None

    def record_delivery(self, delivery_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO webhook_deliveries (delivery_id, received_at) "
                "VALUES (?, ?)",
                (delivery_id, now_iso()),
            )


# ---------------------------------------------------------------------------
# Row helpers (module-private)
# ---------------------------------------------------------------------------

def _row_to_event_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    d["evidence"] = json.loads(d.get("evidence") or "{}")
    return d


def _row_to_intent(row: sqlite3.Row) -> Intent:
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
        semantic=json.loads(d["semantic"]),
        technical=json.loads(d["technical"]),
        checks_required=json.loads(d["checks_required"]),
        dependencies=json.loads(d["dependencies"]),
        retries=d["retries"],
        tenant_id=d.get("tenant_id"),
    )
