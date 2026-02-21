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

from converge import analytics, event_log, projections
from converge.api.auth import require_viewer

# --- Display/query limits ---
_RISK_TREND_LIMIT = 50          # max risk trend entries in dashboard
_EXPORT_INTENT_LIMIT = 100000   # max intents in decision export

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard(
    request: Request,
    tenant_id: str | None = None,
    risk_trend_days: int = 30,
    principal: dict = Depends(require_viewer),
):
    """Operational dashboard: health, risk trends, queue state, compliance, predictions."""
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id

    health = projections.repo_health(db, tenant_id=tenant)
    queue = projections.queue_state(db, tenant_id=tenant)
    compliance = projections.compliance_report(db, tenant_id=tenant)
    risk_trends = projections.risk_trend(db, tenant_id=tenant, days=risk_trend_days)
    predictions = projections.predict_issues(db, tenant_id=tenant)
    metrics = projections.integration_metrics(db, tenant_id=tenant)

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
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id

    compliance = projections.compliance_report(db, tenant_id=tenant)
    predictions = projections.predict_issues(db, tenant_id=tenant)

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


@router.get("/export/decisions")
def export_decisions_http(
    request: Request,
    tenant_id: str | None = None,
    fmt: str = "jsonl",
    principal: dict = Depends(require_viewer),
):
    """Export decision dataset via HTTP (JSONL or CSV)."""
    db = request.app.state.db_path
    tenant = principal.get("tenant") or tenant_id

    intents = event_log.list_intents(db, tenant_id=tenant, limit=_EXPORT_INTENT_LIMIT)
    records = [analytics._build_decision_record(db, intent) for intent in intents]

    if fmt == "csv":
        return _csv_response(records)

    lines = [json.dumps(r, default=str) for r in records]
    return PlainTextResponse(
        "\n".join(lines),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=decisions.jsonl"},
    )


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
