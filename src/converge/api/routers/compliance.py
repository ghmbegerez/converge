"""Compliance report, alerts, and threshold endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from converge import event_log, projections
from converge.api.auth import enforce_tenant, require_operator, require_viewer
from converge.models import Event, EventType

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.get("/report")
def compliance_report(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return projections.compliance_report(db, tenant_id=tenant).to_dict()


@router.get("/alerts")
def compliance_alerts(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    report = projections.compliance_report(db, tenant_id=tenant)
    return report.alerts


@router.get("/thresholds")
def list_thresholds(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return event_log.list_compliance_thresholds(db, tenant_id=tenant)


@router.post("/thresholds")
def upsert_thresholds(
    request: Request,
    body: dict[str, Any],
    principal: dict = Depends(require_viewer),
):
    db = request.app.state.db_path
    tid = enforce_tenant(body.get("tenant_id") or None, principal)
    event_log.upsert_compliance_thresholds(db, tid, body)
    event_log.append(db, Event(
        event_type=EventType.COMPLIANCE_THRESHOLDS_UPDATED,
        tenant_id=tid,
        payload=body,
    ))
    return {"ok": True, "tenant_id": tid}


@router.get("/thresholds/history")
def thresholds_history(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_operator),
):
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id
    return event_log.query(
        db, event_type=EventType.COMPLIANCE_THRESHOLDS_UPDATED, tenant_id=tenant, limit=50,
    )
