"""Core store mixins: events, intents, and commit links.

These mixin classes provide the business methods for EventStorePort,
IntentStorePort, and CommitLinkStorePort.  They rely on ``_StoreDialect``
methods (``_connection``, ``_ph``, ``_placeholders``, etc.) being available
via MRO.
"""

from __future__ import annotations

import json
from typing import Any

from converge.models import Event, Intent, Status, now_iso

_ALLOWED_FILTER_COLS = {"event_type", "intent_id", "agent_id", "tenant_id", "trace_id"}


# ---------------------------------------------------------------------------
# EventStoreMixin
# ---------------------------------------------------------------------------

class EventStoreMixin:
    """Mixin providing EventStorePort methods."""

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


# ---------------------------------------------------------------------------
# IntentStoreMixin
# ---------------------------------------------------------------------------

class IntentStoreMixin:
    """Mixin providing IntentStorePort methods."""

    def upsert_intent(self, intent: Intent) -> None:
        ph = self._ph
        ex = self._excluded_prefix
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO intents (id, source, target, status, created_at, created_by, "
                f"risk_level, priority, semantic, technical, checks_required, dependencies, "
                f"retries, tenant_id, plan_id, origin_type, updated_at) "
                f"VALUES ({self._placeholders(17)}) "
                f"ON CONFLICT(id) DO UPDATE SET "
                f"source={ex}.source, target={ex}.target, status={ex}.status, "
                f"risk_level={ex}.risk_level, priority={ex}.priority, "
                f"semantic={ex}.semantic, technical={ex}.technical, "
                f"checks_required={ex}.checks_required, "
                f"dependencies={ex}.dependencies, retries={ex}.retries, "
                f"tenant_id={ex}.tenant_id, plan_id={ex}.plan_id, "
                f"origin_type={ex}.origin_type, updated_at={ex}.updated_at",
                (
                    intent.id, intent.source, intent.target, intent.status.value,
                    intent.created_at, intent.created_by, intent.risk_level.value,
                    intent.priority, json.dumps(intent.semantic),
                    json.dumps(intent.technical),
                    json.dumps(intent.checks_required),
                    json.dumps(intent.dependencies),
                    intent.retries, intent.tenant_id, intent.plan_id,
                    intent.origin_type, now_iso(),
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
        where, params = self._build_where({
            "status": status, "tenant_id": tenant_id, "source": source,
        })
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM intents{where} ORDER BY priority ASC, created_at ASC LIMIT {self._ph}",
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


# ---------------------------------------------------------------------------
# CommitLinkStoreMixin
# ---------------------------------------------------------------------------

class CommitLinkStoreMixin:
    """Mixin providing CommitLinkStorePort methods."""

    def upsert_commit_link(
        self, intent_id: str, repo: str, sha: str, role: str, observed_at: str,
    ) -> None:
        ph = self._ph
        ex = self._excluded_prefix
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO intent_commit_links (intent_id, repo, sha, role, observed_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}) "
                f"ON CONFLICT(intent_id, sha, role) DO UPDATE SET "
                f"repo={ex}.repo, observed_at={ex}.observed_at",
                (intent_id, repo, sha, role, observed_at),
            )
            conn.commit()

    def list_commit_links(self, intent_id: str) -> list[dict[str, Any]]:
        ph = self._ph
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM intent_commit_links WHERE intent_id = {ph} "
                f"ORDER BY observed_at ASC",
                (intent_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_commit_link(
        self, intent_id: str, sha: str, role: str,
    ) -> bool:
        ph = self._ph
        with self._connection() as conn:
            cur = conn.execute(
                f"DELETE FROM intent_commit_links "
                f"WHERE intent_id = {ph} AND sha = {ph} AND role = {ph}",
                (intent_id, sha, role),
            )
            conn.commit()
        return cur.rowcount > 0
