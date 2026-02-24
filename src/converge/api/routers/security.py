"""Security scanning API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from converge import event_log
from converge.api.auth import require_operator, require_viewer
from converge.event_types import EventType

router = APIRouter(prefix="/security", tags=["security"])


@router.get("/findings")
def list_findings(
    request: Request,
    intent_id: str | None = None,
    scanner: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    tenant_id: str | None = None,
    limit: int = 100,
    principal: dict = Depends(require_viewer),
):
    """List security findings with optional filters."""
    tenant = principal.get("tenant") or tenant_id
    findings = event_log.list_security_findings(
        intent_id=intent_id, scanner=scanner,
        severity=severity, category=category,
        tenant_id=tenant, limit=limit,
    )
    return {"findings": findings, "total": len(findings)}


@router.get("/findings/counts")
def finding_counts(
    request: Request,
    intent_id: str | None = None,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    """Finding counts grouped by severity."""
    tenant = principal.get("tenant") or tenant_id
    return event_log.count_security_findings(
        intent_id=intent_id, tenant_id=tenant,
    )


@router.get("/scans")
def scan_history(
    request: Request,
    intent_id: str | None = None,
    tenant_id: str | None = None,
    limit: int = 20,
    principal: dict = Depends(require_viewer),
):
    """Recent scan history."""
    tenant = principal.get("tenant") or tenant_id
    scans = event_log.query(
        event_type=EventType.SECURITY_SCAN_COMPLETED,
        intent_id=intent_id,
        tenant_id=tenant,
        limit=limit,
    )
    return {"scans": scans, "total": len(scans)}


@router.get("/summary")
def security_summary(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    """Security summary for dashboard: finding counts + recent scans."""
    from converge import security
    tenant = principal.get("tenant") or tenant_id
    return security.scan_summary(tenant_id=tenant)


@router.post("/scan")
def trigger_scan(
    request: Request,
    body: dict,
    principal: dict = Depends(require_operator),
):
    """Trigger a security scan on a path."""
    from converge import security
    path = body.get("path", ".")
    intent_id = body.get("intent_id")
    tenant_id = body.get("tenant_id") or principal.get("tenant")
    return security.run_scan(
        path, intent_id=intent_id, tenant_id=tenant_id,
    )
