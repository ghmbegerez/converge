"""GitHub webhook receiver endpoint."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from converge import event_log
from converge.api.auth import _auth_required, _verify_github_signature
from converge.models import Event, EventType, Intent, Status

router = APIRouter(tags=["webhooks"])


@router.post("/integrations/github/webhook")
async def github_webhook(request: Request):
    """Receive and process GitHub webhook deliveries."""
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    sig = headers.get("x-hub-signature-256", "")
    event_type = headers.get("x-github-event", "")
    delivery_id = headers.get("x-github-delivery", "")

    webhook_secret = request.app.state.webhook_secret
    db_path = request.app.state.db_path

    if not webhook_secret:
        if _auth_required():
            raise HTTPException(
                status_code=403,
                detail="Webhook signature verification not configured",
            )
    elif not _verify_github_signature(webhook_secret, body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Idempotency
    if delivery_id and event_log.is_duplicate_delivery(db_path, delivery_id):
        return {"ok": True, "delivery_id": delivery_id, "duplicate": True}

    data = json.loads(body)

    event_log.append(db_path, Event(
        event_type=EventType.WEBHOOK_RECEIVED,
        payload={
            "github_event": event_type,
            "delivery_id": delivery_id,
            "action": data.get("action", ""),
        },
        evidence={"delivery_id": delivery_id},
    ))
    if delivery_id:
        event_log.record_delivery(db_path, delivery_id)

    if event_type == "pull_request" and data.get("action") in ("opened", "synchronize"):
        pr = data.get("pull_request", {})
        source = pr.get("head", {}).get("ref", "")
        target = pr.get("base", {}).get("ref", "main")
        pr_number = pr.get("number", 0)
        repo_full_name = data.get("repository", {}).get("full_name", "")
        tenant = os.environ.get("CONVERGE_GITHUB_DEFAULT_TENANT")

        intent_id = f"{repo_full_name}:pr-{pr_number}" if repo_full_name else f"pr-{pr_number}"

        intent = Intent(
            id=intent_id,
            source=source,
            target=target,
            status=Status.READY,
            created_by="github-webhook",
            tenant_id=tenant,
            semantic={"problem_statement": pr.get("title", ""), "objective": pr.get("title", "")},
            technical={
                "source_ref": source,
                "target_ref": target,
                "initial_base_commit": pr.get("base", {}).get("sha", ""),
                "repo": repo_full_name,
            },
        )
        event_log.upsert_intent(db_path, intent)
        event_log.append(db_path, Event(
            event_type=EventType.INTENT_CREATED,
            intent_id=intent.id,
            tenant_id=tenant,
            payload=intent.to_dict(),
        ))

    return {"ok": True, "delivery_id": delivery_id}
