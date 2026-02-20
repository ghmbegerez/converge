"""Risk, impact, and diagnostics endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from converge import analytics, event_log
from converge.api.auth import enforce_tenant, require_viewer
from converge.api.schemas import RiskPolicyBody
from converge.models import EventType

router = APIRouter(tags=["risk"])


@router.get("/risk/recent")
def risk_recent(
    request: Request,
    limit: int = 50,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return event_log.query(db, event_type=EventType.RISK_EVALUATED, tenant_id=tenant, limit=limit)


@router.get("/risk/review")
def risk_review(
    request: Request,
    intent_id: str | None = None,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    if not intent_id:
        raise HTTPException(status_code=400, detail="intent_id required")
    return analytics.risk_review(db, intent_id, tenant_id=tenant)


@router.get("/risk/shadow/recent")
def risk_shadow_recent(
    request: Request,
    limit: int = 50,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return event_log.query(db, event_type=EventType.RISK_SHADOW_EVALUATED, tenant_id=tenant, limit=limit)


@router.get("/risk/gate/report")
def risk_gate_report(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    events = event_log.query(db, event_type=EventType.POLICY_EVALUATED, tenant_id=tenant, limit=1000)
    blocked = [e for e in events if e["payload"].get("verdict") == "BLOCK"]
    return {
        "total_evaluations": len(events),
        "total_blocked": len(blocked),
        "block_rate": round(len(blocked) / max(len(events), 1), 3),
        "recent_blocks": blocked[:20],
    }


@router.get("/risk/policy")
def risk_policy_list(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return event_log.list_risk_policies(db, tenant_id=tenant)


@router.post("/risk/policy")
def risk_policy_upsert(
    request: Request,
    body: RiskPolicyBody,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tid = enforce_tenant(body.tenant_id or tenant_id, principal)
    event_log.upsert_risk_policy(db, tid, body.model_dump(exclude_none=True))
    return {"ok": True, "tenant_id": tid}


@router.get("/impact/edges")
def impact_edges(
    request: Request,
    intent_id: str | None = None,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    events = event_log.query(
        db, event_type=EventType.RISK_EVALUATED, intent_id=intent_id, tenant_id=tenant, limit=1,
    )
    edges = events[0]["payload"].get("impact_edges", []) if events else []
    return edges


@router.get("/diagnostics/recent")
def diagnostics_recent(
    request: Request,
    intent_id: str | None = None,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    events = event_log.query(
        db, event_type=EventType.RISK_EVALUATED, intent_id=intent_id, tenant_id=tenant, limit=1,
    )
    if events:
        return events[0]["payload"].get("findings", [])
    return []
