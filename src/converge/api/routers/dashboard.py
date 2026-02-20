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
from converge.models import EventType

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
        "risk_trend": risk_trends[:50],
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

    # Build records inline (analytics.export_decisions writes to file)
    intents = event_log.list_intents(db, tenant_id=tenant, limit=100000)
    records = []

    for intent in intents:
        risk_events = event_log.query(db, event_type=EventType.RISK_EVALUATED, intent_id=intent.id, limit=1)
        sim_events = event_log.query(db, event_type=EventType.SIMULATION_COMPLETED, intent_id=intent.id, limit=1)
        policy_events = event_log.query(db, event_type=EventType.POLICY_EVALUATED, intent_id=intent.id, limit=1)

        risk_data = risk_events[0]["payload"] if risk_events else {}
        sim_data = sim_events[0]["payload"] if sim_events else {}
        policy_data = policy_events[0]["payload"] if policy_events else {}
        signals = risk_data.get("signals", {})

        records.append({
            "intent_id": intent.id,
            "source": intent.source,
            "target": intent.target,
            "status": intent.status.value,
            "risk_level": intent.risk_level.value,
            "priority": intent.priority,
            "retries": intent.retries,
            "tenant_id": intent.tenant_id,
            "created_at": intent.created_at,
            "mergeable": sim_data.get("mergeable"),
            "conflict_count": len(sim_data.get("conflicts", [])),
            "files_changed_count": len(sim_data.get("files_changed", [])),
            "risk_score": risk_data.get("risk_score"),
            "damage_score": risk_data.get("damage_score"),
            "entropy_score": risk_data.get("entropy_score"),
            "propagation_score": risk_data.get("propagation_score"),
            "containment_score": risk_data.get("containment_score"),
            "entropic_load": signals.get("entropic_load"),
            "contextual_value": signals.get("contextual_value"),
            "complexity_delta": signals.get("complexity_delta"),
            "path_dependence": signals.get("path_dependence"),
            "bomb_count": len(risk_data.get("bombs", [])),
            "bomb_types": [b.get("type") for b in risk_data.get("bombs", [])],
            "policy_verdict": policy_data.get("verdict"),
            "policy_profile": policy_data.get("profile_used"),
        })

    if fmt == "csv":
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

    # JSONL
    lines = [json.dumps(r, default=str) for r in records]
    return PlainTextResponse(
        "\n".join(lines),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=decisions.jsonl"},
    )
