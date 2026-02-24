"""GitHub webhook receiver endpoint.

Infrastructure only: size guard, signature verification, idempotency,
audit logging, and dispatch.  All domain logic lives in
:mod:`converge.api.routers.github_events`.
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request

from converge import event_log
from converge.api.auth import _auth_required, _verify_github_signature
from converge.api.routers.github_events import dispatch_github_event
from converge.models import Event, EventType

log = logging.getLogger("converge.webhooks")

router = APIRouter(tags=["webhooks"])


def _parse_max_body() -> int:
    """Parse CONVERGE_WEBHOOK_MAX_BODY_BYTES safely; default 1 MiB."""
    raw = os.environ.get("CONVERGE_WEBHOOK_MAX_BODY_BYTES", "1048576")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1048576


_MAX_WEBHOOK_BODY = _parse_max_body()


@router.post("/integrations/github/webhook")
async def github_webhook(request: Request):
    """Receive and process GitHub webhook deliveries."""
    # --- Size guard ---
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_WEBHOOK_BODY:
        raise HTTPException(status_code=413, detail="Payload too large")
    body = await request.body()
    if len(body) > _MAX_WEBHOOK_BODY:
        raise HTTPException(status_code=413, detail="Payload too large")

    headers = {k.lower(): v for k, v in request.headers.items()}
    sig = headers.get("x-hub-signature-256", "")
    event_type = headers.get("x-github-event", "")
    delivery_id = headers.get("x-github-delivery", "")

    # --- Signature verification ---
    webhook_secret = request.app.state.webhook_secret

    if not webhook_secret:
        if _auth_required():
            raise HTTPException(
                status_code=403,
                detail="Webhook signature verification not configured",
            )
        log.warning("Webhook accepted without signature verification (no secret configured)")
    elif not _verify_github_signature(webhook_secret, body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # --- Idempotency ---
    if delivery_id and event_log.is_duplicate_delivery(delivery_id):
        return {"ok": True, "delivery_id": delivery_id, "duplicate": True}

    data = json.loads(body)

    # --- Audit log ---
    event_log.append(Event(
        event_type=EventType.WEBHOOK_RECEIVED,
        payload={
            "github_event": event_type,
            "delivery_id": delivery_id,
            "action": data.get("action", ""),
        },
        evidence={"delivery_id": delivery_id},
    ))
    if delivery_id:
        event_log.record_delivery(delivery_id)

    # --- Dispatch to domain handlers ---
    return await dispatch_github_event(event_type, data, delivery_id)
