"""Intent, summary, auth, key rotation, and prediction endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from converge import event_log, projections
from converge.api.auth import require_admin, require_viewer, rotate_key
from converge.api.schemas import KeyRotateBody

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
