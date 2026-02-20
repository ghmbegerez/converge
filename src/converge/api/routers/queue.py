"""Queue state endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from converge import projections
from converge.api.auth import require_viewer

router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("/state")
def queue_state(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return projections.queue_state(db, tenant_id=tenant).to_dict()


@router.get("/summary")
def queue_summary(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    qs = projections.queue_state(db, tenant_id=tenant)
    return {"total": qs.total, "by_status": qs.by_status, "pending_count": len(qs.pending)}
