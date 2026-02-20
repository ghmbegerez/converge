"""Agent policy and authorization endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from converge import agents
from converge.api.auth import require_admin, require_viewer
from converge.api.schemas import AgentAuthorizeBody, AgentPolicyBody
from converge.models import AgentPolicy

router = APIRouter(prefix="/agent", tags=["agents"])


@router.get("/policy")
def list_policies(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return agents.list_policies(db, tenant_id=tenant)


@router.post("/policy")
def set_policy(
    request: Request,
    body: AgentPolicyBody,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    pol = AgentPolicy.from_dict(body.model_dump())
    return agents.set_policy(db, pol)


@router.post("/authorize")
def authorize(
    request: Request,
    body: AgentAuthorizeBody,
    principal: dict = Depends(require_admin),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant")
    return agents.authorize(
        db,
        agent_id=body.agent_id,
        action=body.action,
        intent_id=body.intent_id,
        tenant_id=body.tenant_id or tenant,
        human_approvals=body.human_approvals,
    )
