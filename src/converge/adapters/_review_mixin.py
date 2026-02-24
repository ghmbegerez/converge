"""Review-related store mixins: review tasks, intake overrides, security findings.

These mixin classes provide the business methods for ReviewStorePort,
IntakeStorePort, and SecurityFindingStorePort.  They rely on
``_StoreDialect`` methods being available via MRO.
"""

from __future__ import annotations

from typing import Any

from converge.models import now_iso


# ---------------------------------------------------------------------------
# ReviewStoreMixin
# ---------------------------------------------------------------------------

class ReviewStoreMixin:
    """Mixin providing ReviewStorePort methods."""

    @staticmethod
    def _row_to_review_task(row: Any) -> "ReviewTask":
        from converge.models import ReviewStatus, ReviewTask, RiskLevel
        d = dict(row)
        return ReviewTask(
            id=d["id"],
            intent_id=d["intent_id"],
            status=ReviewStatus(d["status"]),
            reviewer=d.get("reviewer"),
            priority=d["priority"],
            risk_level=RiskLevel(d["risk_level"]) if d.get("risk_level") else RiskLevel.MEDIUM,
            trigger=d.get("trigger", "policy"),
            sla_deadline=d.get("sla_deadline"),
            created_at=d["created_at"],
            assigned_at=d.get("assigned_at"),
            completed_at=d.get("completed_at"),
            escalated_at=d.get("escalated_at"),
            resolution=d.get("resolution"),
            notes=d.get("notes", ""),
            tenant_id=d.get("tenant_id"),
        )

    def upsert_review_task(self, task: "ReviewTask") -> None:
        from converge.models import ReviewTask  # noqa: F811
        ph = self._ph
        ex = self._excluded_prefix
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO review_tasks "
                f"(id, intent_id, status, reviewer, priority, risk_level, "
                f"trigger, sla_deadline, created_at, assigned_at, completed_at, "
                f"escalated_at, resolution, notes, tenant_id) "
                f"VALUES ({self._placeholders(15)}) "
                f"ON CONFLICT(id) DO UPDATE SET "
                f"status={ex}.status, reviewer={ex}.reviewer, "
                f"priority={ex}.priority, risk_level={ex}.risk_level, "
                f"sla_deadline={ex}.sla_deadline, "
                f"assigned_at={ex}.assigned_at, completed_at={ex}.completed_at, "
                f"escalated_at={ex}.escalated_at, resolution={ex}.resolution, "
                f"notes={ex}.notes",
                (
                    task.id, task.intent_id, task.status.value,
                    task.reviewer, task.priority, task.risk_level.value,
                    task.trigger, task.sla_deadline, task.created_at,
                    task.assigned_at, task.completed_at, task.escalated_at,
                    task.resolution, task.notes, task.tenant_id,
                ),
            )
            conn.commit()

    def get_review_task(self, task_id: str) -> "ReviewTask | None":
        ph = self._ph
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT * FROM review_tasks WHERE id = {ph}", (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_review_task(row)

    def list_review_tasks(
        self,
        *,
        intent_id: str | None = None,
        status: str | None = None,
        reviewer: str | None = None,
        tenant_id: str | None = None,
        limit: int = 200,
    ) -> list["ReviewTask"]:
        where, params = self._build_where({
            "intent_id": intent_id, "status": status,
            "reviewer": reviewer, "tenant_id": tenant_id,
        })
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM review_tasks{where} "
                f"ORDER BY priority ASC, created_at ASC LIMIT {self._ph}",
                params,
            ).fetchall()
        return [self._row_to_review_task(r) for r in rows]

    def update_review_task_status(
        self, task_id: str, status: str, **fields: Any,
    ) -> None:
        ph = self._ph
        sets = [f"status = {ph}"]
        params: list[Any] = [status]
        for col in ("reviewer", "assigned_at", "completed_at",
                     "escalated_at", "resolution", "notes"):
            if col in fields:
                sets.append(f"{col} = {ph}")
                params.append(fields[col])
        params.append(task_id)
        sql = f"UPDATE review_tasks SET {', '.join(sets)} WHERE id = {ph}"
        with self._connection() as conn:
            conn.execute(sql, params)
            conn.commit()


# ---------------------------------------------------------------------------
# IntakeStoreMixin
# ---------------------------------------------------------------------------

class IntakeStoreMixin:
    """Mixin providing IntakeStorePort methods."""

    def upsert_intake_override(
        self, tenant_id: str, mode: str, set_by: str, reason: str,
    ) -> None:
        ex = self._excluded_prefix
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO intake_overrides (tenant_id, mode, set_by, set_at, reason) "
                f"VALUES ({self._placeholders(5)}) "
                f"ON CONFLICT(tenant_id) DO UPDATE SET "
                f"mode={ex}.mode, set_by={ex}.set_by, set_at={ex}.set_at, reason={ex}.reason",
                (tenant_id, mode, set_by, now_iso(), reason),
            )
            conn.commit()

    def get_intake_override(self, tenant_id: str) -> dict[str, Any] | None:
        ph = self._ph
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT mode, set_by, set_at, reason FROM intake_overrides "
                f"WHERE tenant_id = {ph}",
                (tenant_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "tenant_id": tenant_id,
            "mode": row["mode"],
            "set_by": row["set_by"],
            "set_at": row["set_at"],
            "reason": row["reason"],
        }

    def delete_intake_override(self, tenant_id: str) -> bool:
        ph = self._ph
        with self._connection() as conn:
            cur = conn.execute(
                f"DELETE FROM intake_overrides WHERE tenant_id = {ph}",
                (tenant_id,),
            )
            conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# SecurityFindingStoreMixin
# ---------------------------------------------------------------------------

class SecurityFindingStoreMixin:
    """Mixin providing SecurityFindingStorePort methods."""

    def upsert_security_finding(self, finding: dict[str, Any]) -> None:
        ph = self._ph
        ex = self._excluded_prefix
        with self._connection() as conn:
            conn.execute(
                f"""INSERT INTO security_findings
                    (id, scanner, category, severity, file, line, rule,
                     evidence, confidence, intent_id, tenant_id, scan_id, timestamp)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph},
                        {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                ON CONFLICT(id) DO UPDATE SET
                    severity={ex}.severity,
                    evidence={ex}.evidence,
                    confidence={ex}.confidence,
                    timestamp={ex}.timestamp""",
                (
                    finding["id"], finding["scanner"], finding["category"],
                    finding["severity"], finding.get("file", ""),
                    finding.get("line", 0), finding.get("rule", ""),
                    finding.get("evidence", ""), finding.get("confidence", "medium"),
                    finding.get("intent_id"), finding.get("tenant_id"),
                    finding.get("scan_id"), finding.get("timestamp", now_iso()),
                ),
            )
            conn.commit()

    def list_security_findings(
        self,
        *,
        intent_id: str | None = None,
        scanner: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        tenant_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where, params = self._build_where({
            "intent_id": intent_id, "scanner": scanner,
            "severity": severity, "category": category, "tenant_id": tenant_id,
        })
        if not where:
            where = " WHERE 1=1"
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM security_findings{where} ORDER BY timestamp DESC LIMIT {self._ph}",
                tuple(params),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_security_findings(
        self,
        *,
        intent_id: str | None = None,
        severity: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, int]:
        where, params = self._build_where({
            "intent_id": intent_id, "severity": severity, "tenant_id": tenant_id,
        })
        if not where:
            where = " WHERE 1=1"
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT severity, COUNT(*) as cnt FROM security_findings{where} GROUP BY severity",
                tuple(params),
            ).fetchall()
        counts = {r["severity"]: r["cnt"] for r in rows}
        counts["total"] = sum(counts.values())
        return counts
