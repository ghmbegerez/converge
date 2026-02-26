"""Intake control API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from converge import intake
from converge.api.auth import require_admin, require_viewer

router = APIRouter(tags=["intake"])


class IntakeModeBody(BaseModel):
    mode: str
    reason: str = ""


@router.get("/intake/status")
def intake_status_http(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    """Current intake mode, thresholds, and health signals."""
    tenant = principal.get("tenant") or tenant_id
    return intake.intake_status(tenant_id=tenant)


@router.get("/intake/mode")
def intake_mode_http(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    """Current intake mode (simplified view for UI)."""
    tenant = principal.get("tenant") or tenant_id
    status = intake.intake_status(tenant_id=tenant)
    return {"mode": status.get("mode", "open")}


@router.post("/intake/mode")
def intake_set_mode_http(
    request: Request,
    body: IntakeModeBody,
    tenant_id: str | None = None,
    principal: dict = Depends(require_admin),
):
    """Manually override intake mode. Use mode='auto' to clear override."""
    tenant = principal.get("tenant") or tenant_id
    actor = principal.get("actor", "operator")
    return intake.set_intake_mode(
        body.mode, tenant_id=tenant, set_by=actor, reason=body.reason,
    )
