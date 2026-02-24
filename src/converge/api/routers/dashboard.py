"""Dashboard and export endpoints.

Aggregates health, risk trends, queue state, compliance, and predictions
into a single view for operational dashboards.
"""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from converge import event_log, exports, projections, security
from converge.defaults import QUERY_LIMIT_UNBOUNDED
from converge.api.auth import require_viewer

# --- Display/query limits ---
_RISK_TREND_LIMIT = 50          # max risk trend entries in dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard(
    request: Request,
    tenant_id: str | None = None,
    risk_trend_days: int = 30,
    principal: dict = Depends(require_viewer),
):
    """Operational dashboard: health, risk trends, queue state, compliance, predictions."""
    tenant = principal.get("tenant") or tenant_id

    health = projections.repo_health(tenant_id=tenant)
    queue = projections.queue_state(tenant_id=tenant)
    compliance = projections.compliance_report(tenant_id=tenant)
    risk_trends = projections.risk_trend(tenant_id=tenant, days=risk_trend_days)
    predictions = projections.predict_issues(tenant_id=tenant)
    metrics = projections.integration_metrics(tenant_id=tenant)

    debt = projections.verification_debt(tenant_id=tenant)
    sec_summary = security.scan_summary(tenant_id=tenant)

    return {
        "health": health.to_dict(),
        "queue": queue.to_dict(),
        "compliance": {
            "passed": compliance.passed,
            "alerts": compliance.alerts,
            "checks": compliance.checks,
            "mergeable_rate": compliance.mergeable_rate,
            "conflict_rate": compliance.conflict_rate,
        },
        "debt": debt.to_dict(),
        "security": sec_summary,
        "risk_trend": risk_trends[:_RISK_TREND_LIMIT],
        "predictions": predictions,
        "metrics": metrics,
    }


@router.get("/dashboard/alerts")
def dashboard_alerts(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    """Compliance alerts + prediction signals for a tenant."""
    tenant = principal.get("tenant") or tenant_id

    compliance = projections.compliance_report(tenant_id=tenant)
    predictions = projections.predict_issues(tenant_id=tenant)

    all_alerts = []
    for alert in compliance.alerts:
        all_alerts.append({**alert, "source": "compliance"})
    for pred in predictions:
        all_alerts.append({
            "signal": pred.get("signal", ""),
            "severity": pred.get("severity", "medium"),
            "message": pred.get("message", ""),
            "recommendation": pred.get("recommendation", ""),
            "source": "prediction",
        })

    return {
        "compliance_passed": compliance.passed,
        "alerts": all_alerts,
        "total": len(all_alerts),
    }


@router.get("/verification/debt")
def verification_debt_http(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    """Current verification debt score with breakdown."""
    tenant = principal.get("tenant") or tenant_id
    debt = projections.verification_debt(tenant_id=tenant)
    return debt.to_dict()


@router.get("/export/decisions")
def export_decisions_http(
    request: Request,
    tenant_id: str | None = None,
    fmt: str = "jsonl",
    principal: dict = Depends(require_viewer),
):
    """Export decision dataset via HTTP (JSONL or CSV)."""
    tenant = principal.get("tenant") or tenant_id

    intents = event_log.list_intents(tenant_id=tenant, limit=QUERY_LIMIT_UNBOUNDED)
    records = [exports._build_decision_record(intent) for intent in intents]

    if fmt == "csv":
        return _csv_response(records)

    lines = [json.dumps(r, default=str) for r in records]
    return PlainTextResponse(
        "\n".join(lines),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=decisions.jsonl"},
    )


@router.get("/reviews")
def reviews_list_http(
    request: Request,
    tenant_id: str | None = None,
    intent_id: str | None = None,
    status: str | None = None,
    reviewer: str | None = None,
    limit: int = 50,
    principal: dict = Depends(require_viewer),
):
    """List review tasks with optional filters."""
    tenant = principal.get("tenant") or tenant_id
    tasks = event_log.list_review_tasks(
        intent_id=intent_id, status=status,
        reviewer=reviewer, tenant_id=tenant, limit=limit,
    )
    return {"reviews": [t.to_dict() for t in tasks], "total": len(tasks)}


@router.get("/reviews/summary")
def reviews_summary_http(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    """Review task summary for dashboard."""
    from converge import reviews
    tenant = principal.get("tenant") or tenant_id
    return reviews.review_summary(tenant_id=tenant)


@router.get("/semantic/conflicts")
def semantic_conflicts_http(
    request: Request,
    tenant_id: str | None = None,
    target: str | None = None,
    model: str = "deterministic-v1",
    similarity_threshold: float = 0.70,
    conflict_threshold: float = 0.60,
    mode: str = "shadow",
    principal: dict = Depends(require_viewer),
):
    """Scan for semantic conflicts between intents."""
    from converge.semantic.conflicts import scan_conflicts
    tenant = principal.get("tenant") or tenant_id
    report = scan_conflicts(
        model=model,
        tenant_id=tenant,
        target=target,
        similarity_threshold=similarity_threshold,
        conflict_threshold=conflict_threshold,
        mode=mode,
    )
    return {
        "conflicts": [
            {
                "intent_a": c.intent_a,
                "intent_b": c.intent_b,
                "score": c.score,
                "similarity": c.similarity,
                "target_overlap": c.target_overlap,
                "scope_overlap": c.scope_overlap,
                "target": c.target,
            }
            for c in report.conflicts
        ],
        "candidates_checked": report.candidates_checked,
        "mode": report.mode,
        "threshold": report.threshold,
        "timestamp": report.timestamp,
    }


@router.get("/semantic/conflicts/active")
def semantic_conflicts_active_http(
    request: Request,
    tenant_id: str | None = None,
    limit: int = 50,
    principal: dict = Depends(require_viewer),
):
    """List active (unresolved) semantic conflicts."""
    from converge.semantic.conflicts import list_conflicts
    tenant = principal.get("tenant") or tenant_id
    return {"conflicts": list_conflicts(tenant_id=tenant, limit=limit)}


@router.get("/semantic/status")
def semantic_status_http(
    request: Request,
    tenant_id: str | None = None,
    model: str | None = None,
    principal: dict = Depends(require_viewer),
):
    """Embedding coverage and status."""
    tenant = principal.get("tenant") or tenant_id
    return event_log.embedding_coverage(tenant_id=tenant, model=model)


def _csv_response(records: list[dict]) -> PlainTextResponse:
    """Format decision records as a CSV HTTP response."""
    if not records:
        return PlainTextResponse("", media_type="text/csv")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=records[0].keys())
    writer.writeheader()
    for r in records:
        r["bomb_types"] = ",".join(r.get("bomb_types") or [])
        writer.writerow(r)
    return PlainTextResponse(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=decisions.csv"},
    )
