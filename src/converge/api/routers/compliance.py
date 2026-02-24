"""Compliance report, alerts, and threshold endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from converge import event_log, projections
from converge.api.auth import enforce_tenant, require_operator, require_viewer
from converge.api.schemas import ComplianceThresholdsBody
from converge.models import Event, EventType

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.get("/report")
def compliance_report(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    tenant = principal.get("tenant") or tenant_id
    return projections.compliance_report(tenant_id=tenant).to_dict()


@router.get("/alerts")
def compliance_alerts(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    tenant = principal.get("tenant") or tenant_id
    report = projections.compliance_report(tenant_id=tenant)
    return report.alerts


@router.get("/thresholds")
def list_thresholds(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    tenant = principal.get("tenant") or tenant_id
    return event_log.list_compliance_thresholds(tenant_id=tenant)


@router.post("/thresholds")
def upsert_thresholds(
    request: Request,
    body: ComplianceThresholdsBody,
    principal: dict = Depends(require_viewer),
):
    tid = enforce_tenant(body.tenant_id or None, principal)
    data = body.model_dump(exclude_none=True)
    event_log.upsert_compliance_thresholds(tid, data)
    event_log.append(Event(
        event_type=EventType.COMPLIANCE_THRESHOLDS_UPDATED,
        tenant_id=tid,
        payload=data,
    ))
    return {"ok": True, "tenant_id": tid}


@router.get("/thresholds/history")
def thresholds_history(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_operator),
):
    tenant = principal.get("tenant") or tenant_id
    return event_log.query(
        event_type=EventType.COMPLIANCE_THRESHOLDS_UPDATED, tenant_id=tenant, limit=50,
    )
