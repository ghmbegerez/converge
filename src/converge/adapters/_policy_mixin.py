"""Policy-related store mixins: agent/risk/compliance policies, locks, deliveries.

These mixin classes provide the business methods for PolicyStorePort,
LockStorePort, and DeliveryStorePort.  They rely on ``_StoreDialect``
methods being available via MRO.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from converge.models import now_iso


# ---------------------------------------------------------------------------
# PolicyStoreMixin
# ---------------------------------------------------------------------------

class PolicyStoreMixin:
    """Mixin providing PolicyStorePort methods."""

    def upsert_agent_policy(self, data: dict[str, Any]) -> None:
        agent_id = data["agent_id"]
        tid = data.get("tenant_id") or ""
        self._upsert_policy(
            "agent_policies", {"agent_id": agent_id, "tenant_id": tid}, data,
        )

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
        where, params = self._build_where({"tenant_id": tenant_id})
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT data FROM agent_policies{where} ORDER BY agent_id",
                params,
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
        where, params = self._build_where({"tenant_id": tenant_id})
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT tenant_id, data, version FROM risk_policies{where} ORDER BY tenant_id",
                params,
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
        self._upsert_policy("compliance_thresholds", {"tenant_id": tenant_id}, data)

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
        where, params = self._build_where({"tenant_id": tenant_id})
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT tenant_id, data FROM compliance_thresholds{where} ORDER BY tenant_id",
                params,
            ).fetchall()
        result = []
        for r in rows:
            d = json.loads(r["data"])
            d["tenant_id"] = r["tenant_id"]
            result.append(d)
        return result


# ---------------------------------------------------------------------------
# LockMixin
# ---------------------------------------------------------------------------

class LockMixin:
    """Mixin providing LockStorePort methods."""

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


# ---------------------------------------------------------------------------
# DeliveryMixin
# ---------------------------------------------------------------------------

class DeliveryMixin:
    """Mixin providing DeliveryStorePort methods."""

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
