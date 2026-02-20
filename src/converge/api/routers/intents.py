"""Intent, summary, auth, key rotation, and prediction endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from converge import event_log, projections
from converge.api.auth import require_admin, require_viewer, rotate_key
from converge.api.schemas import KeyRotateBody
from converge.models import now_iso

router = APIRouter(tags=["intents"])


@router.get("/intents")
def list_intents(
    request: Request,
    status: str | None = None,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    intents = event_log.list_intents(db, status=status, tenant_id=tenant)
    return [i.to_dict() for i in intents]


@router.get("/summary")
def summary(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    health = projections.repo_health(db, tenant_id=tenant)
    qs = projections.queue_state(db, tenant_id=tenant)
    return {"health": health.to_dict(), "queue": qs.to_dict()}


@router.get("/auth/whoami")
def whoami(principal: dict = Depends(require_viewer)):
    return principal


@router.get("/predictions")
def predictions(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return projections.predict_issues(db, tenant_id=tenant)


@router.post("/auth/keys/rotate")
def rotate_api_key(
    request: Request,
    body: KeyRotateBody,
    principal: dict = Depends(require_admin),
):
    return rotate_key(request, grace_period_seconds=body.grace_period_seconds)
