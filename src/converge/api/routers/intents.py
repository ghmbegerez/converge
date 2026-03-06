"""Intent, summary, auth, key rotation, and prediction endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from converge import engine, event_log, projections
from converge.api.auth import require_admin, require_operator, require_viewer, rotate_key
from converge.api.schemas import KeyRotateBody
from converge.intake import evaluate_intake
from converge.models import Event, EventType, Intent, RiskLevel, Status, new_id, now_iso

router = APIRouter(tags=["intents"])


@router.get("/intents")
def list_intents(
    request: Request,
    status: str | None = None,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    tenant = principal.get("tenant") or tenant_id
    intents = event_log.list_intents(status=status, tenant_id=tenant)
    return [i.to_dict() for i in intents]


@router.get("/intents/{intent_id}")
def get_intent(
    intent_id: str,
    request: Request,
    principal: dict = Depends(require_viewer),
):
    intent = event_log.get_intent(intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="Intent not found")
    result = intent.to_dict()
    result["commit_links"] = event_log.list_commit_links(intent_id)
    return result


@router.get("/intents/{intent_id}/events")
def intent_events(
    intent_id: str,
    request: Request,
    limit: int = 200,
    principal: dict = Depends(require_viewer),
):
    """Return the event timeline for a single intent, ordered by timestamp."""
    intent = event_log.get_intent(intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="Intent not found")
    return event_log.query(intent_id=intent_id, limit=limit)


@router.get("/summary")
def summary(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    tenant = principal.get("tenant") or tenant_id
    health = projections.repo_health(tenant_id=tenant)
    qs = projections.queue_state(tenant_id=tenant)
    return {"health": health.to_dict(), "queue": qs.to_dict()}


@router.get("/auth/whoami")
def whoami(principal: dict = Depends(require_viewer)):
    return principal


@router.get("/predictions")
def predictions(
    request: Request,
    tenant_id: str | None = None,
    principal: dict = Depends(require_viewer),
):
    tenant = principal.get("tenant") or tenant_id
    return projections.predict_issues(tenant_id=tenant)


@router.post("/intents/evaluate")
def evaluate_intent_pre(
    request: Request,
    body: dict[str, Any],
    principal: dict = Depends(require_viewer),
):
    """Pre-evaluate a draft intent before creation."""
    from converge import harness
    mode = body.pop("mode", "shadow")
    cfg = harness.HarnessConfig(mode=mode)
    result = harness.evaluate_intent(body, config=cfg)
    return result.to_dict()


@router.post("/intents")
def create_intent(
    request: Request,
    body: dict[str, Any],
    principal: dict = Depends(require_operator),
):
    """Create a new intent."""
    tenant = body.get("tenant_id") or principal.get("tenant")

    # Support both flat and nested field formats
    source = body.get("source") or body.get("technical", {}).get("source_ref", "")
    target = body.get("target") or body.get("technical", {}).get("target_ref", "main")

    if not source:
        raise HTTPException(status_code=400, detail="source is required")

    intent_id = body.get("id") or body.get("intent_id") or f"api-{new_id()}"

    intent = Intent(
        id=intent_id,
        source=source,
        target=target,
        status=Status(body.get("status", "READY")),
        created_at=now_iso(),
        created_by=principal.get("actor", "api"),
        risk_level=RiskLevel(body.get("risk_level", "medium")),
        priority=body.get("priority", 3),
        semantic=body.get("semantic", {}),
        technical=body.get("technical", {}),
        checks_required=body.get("checks_required", []),
        dependencies=body.get("dependencies", []),
        tenant_id=tenant,
        plan_id=body.get("plan_id"),
        origin_type=body.get("origin_type", "api"),
    )

    # Intake pre-check (system health evaluation)
    decision = evaluate_intake(intent)
    if not decision.accepted:
        return {
            "ok": False,
            "intent_id": intent.id,
            "rejected_by": "intake",
            "mode": decision.mode.value,
            "reason": decision.reason,
        }

    event_log.upsert_intent(intent)
    event_log.append(Event(
        event_type=EventType.INTENT_CREATED,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=intent.to_dict(),
    ))
    return {"ok": True, "intent_id": intent.id, "status": intent.status.value}


@router.post("/intents/{intent_id}/validate")
def validate_intent_http(
    intent_id: str,
    request: Request,
    body: dict[str, Any] | None = None,
    principal: dict = Depends(require_operator),
):
    """Run full validation: simulate + check + policy + risk."""
    intent = event_log.get_intent(intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="Intent not found")

    body = body or {}
    modified = False
    if body.get("source"):
        intent.source = body["source"]
        modified = True
    if body.get("target"):
        intent.target = body["target"]
        modified = True
    if modified:
        event_log.upsert_intent(intent)

    return engine.validate_intent(
        intent,
        use_last_simulation=body.get("use_last_simulation", False),
        skip_checks=body.get("skip_checks", False),
    )


@router.get("/flags")
def list_flags_http(
    principal: dict = Depends(require_viewer),
):
    """List all feature flags."""
    from converge import feature_flags
    return {"flags": feature_flags.list_flags()}


@router.post("/flags/{flag_name}")
def set_flag_http(
    flag_name: str,
    request: Request,
    body: dict[str, Any],
    principal: dict = Depends(require_admin),
):
    """Set a feature flag at runtime."""
    from converge import feature_flags
    state = feature_flags.set_flag(
        flag_name,
        enabled=body.get("enabled"),
        mode=body.get("mode"),
    )
    if state is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown flag: {flag_name}")
    return state.to_dict()


@router.post("/auth/keys/rotate")
def rotate_api_key(
    request: Request,
    body: KeyRotateBody,
    principal: dict = Depends(require_admin),
):
    return rotate_key(request, grace_period_seconds=body.grace_period_seconds)
