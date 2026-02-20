"""Event query, audit, policy-recent, metrics, and health projection endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from converge import event_log, projections
from converge.api.auth import require_operator, require_viewer
from converge.models import EventType

router = APIRouter(tags=["events"])


@router.get("/events")
def query_events(
    request: Request,
    type: str | None = None,
    intent_id: str | None = None,
    agent_id: str | None = None,
    tenant_id: str | None = None,
    since: str | None = None,
    limit: int = 100,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return event_log.query(
        db,
        event_type=type,
        intent_id=intent_id,
        agent_id=agent_id,
        tenant_id=tenant,
        since=since,
        limit=limit,
    )


@router.get("/audit/recent")
def audit_recent(
    request: Request,
    limit: int = 100,
    tenant_id: str | None = None,
    principal: dict = Depends(require_operator),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return event_log.query(db, tenant_id=tenant, limit=limit)


@router.get("/policy/recent")
def policy_recent(
    request: Request,
    limit: int = 50,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return event_log.query(
        db, event_type=EventType.POLICY_EVALUATED, tenant_id=tenant, limit=limit,
    )


@router.get("/metrics/integration")
def metrics_integration(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return projections.integration_metrics(db, tenant_id=tenant)


# ---------------------------------------------------------------------------
# Health projections (project health, not server health)
# ---------------------------------------------------------------------------

@router.get("/health/repo/now")
def health_repo_now(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return projections.repo_health(db, tenant_id=tenant).to_dict()


@router.get("/health/repo/trend")
def health_repo_trend(
    request: Request,
    days: int = 30,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return projections.health_trend(db, tenant_id=tenant, days=days)


@router.get("/health/change")
def health_change(
    request: Request,
    intent_id: str | None = None,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    if not intent_id:
        raise HTTPException(status_code=400, detail="intent_id required")
    return projections.change_health(db, intent_id, tenant_id=tenant)


@router.get("/health/change/trend")
def health_change_trend(
    request: Request,
    days: int = 30,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return projections.change_health_trend(db, tenant_id=tenant, days=days)


@router.get("/health/entropy/trend")
def health_entropy_trend(
    request: Request,
    days: int = 30,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return projections.entropy_trend(db, tenant_id=tenant, days=days)
