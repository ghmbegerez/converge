"""GitHub webhook receiver endpoint with bidirectional sync.

Handles:
  - pull_request opened/synchronize → create/update intent + trigger validation
  - pull_request closed → update intent (MERGED if merged, REJECTED if closed)
  - push on source branch → revalidate associated intent
  - merge_group checks_requested → create intent for merge queue candidate
  - merge_group destroyed → reject intent (dequeued / checks failed)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from converge import event_log
from converge.api.auth import _auth_required, _verify_github_signature
from converge.integrations.github_publish import try_publish_decision
from converge.models import Event, EventType, Intent, Status

log = logging.getLogger("converge.webhooks")

# --- Display constants ---
_SHA_DISPLAY_LEN = 12           # characters of SHA shown in intent IDs

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

    webhook_secret = request.app.state.webhook_secret
    db_path = request.app.state.db_path

    if not webhook_secret:
        if _auth_required():
            raise HTTPException(
                status_code=403,
                detail="Webhook signature verification not configured",
            )
        log.warning("Webhook accepted without signature verification (no secret configured)")
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

    # ---------------------------------------------------------------
    # pull_request events
    # ---------------------------------------------------------------
    if event_type == "pull_request":
        action = data.get("action", "")
        pr = data.get("pull_request", {})
        pr_number = pr.get("number", 0)
        repo_full_name = data.get("repository", {}).get("full_name", "")
        intent_id = f"{repo_full_name}:pr-{pr_number}" if repo_full_name else f"pr-{pr_number}"

        if action in ("opened", "synchronize", "reopened"):
            return await _handle_pr_opened(db_path, data, pr, intent_id, repo_full_name)

        if action == "closed":
            return await _handle_pr_closed(db_path, pr, intent_id, repo_full_name)

    # ---------------------------------------------------------------
    # push events → revalidate if branch matches an open intent
    # ---------------------------------------------------------------
    if event_type == "push":
        return await _handle_push(db_path, data)

    # ---------------------------------------------------------------
    # merge_group events → GitHub Merge Queue integration
    # ---------------------------------------------------------------
    if event_type == "merge_group":
        return await _handle_merge_group(db_path, data)

    return {"ok": True, "delivery_id": delivery_id}


# ---------------------------------------------------------------------------
# PR opened / synchronize / reopened
# ---------------------------------------------------------------------------

async def _handle_pr_opened(
    db_path: str,
    data: dict[str, Any],
    pr: dict[str, Any],
    intent_id: str,
    repo_full_name: str,
) -> dict[str, Any]:
    """Create or update intent from PR, set status to READY."""
    source = pr.get("head", {}).get("ref", "")
    target = pr.get("base", {}).get("ref", "main")
    head_sha = pr.get("head", {}).get("sha", "")

    if not head_sha or not source:
        return {"ok": True, "intent_id": intent_id, "action": "ignored", "reason": "missing_head_sha_or_ref"}

    tenant = os.environ.get("CONVERGE_GITHUB_DEFAULT_TENANT")

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
            "initial_base_commit": head_sha,
            "repo": repo_full_name,
            "pr_number": pr.get("number", 0),
            "installation_id": data.get("installation", {}).get("id"),
        },
    )
    event_log.upsert_intent(db_path, intent)
    event_log.append(db_path, Event(
        event_type=EventType.INTENT_CREATED,
        intent_id=intent.id,
        tenant_id=tenant,
        payload=intent.to_dict(),
    ))

    event_installation_id = data.get("installation", {}).get("id")
    await try_publish_decision(
        repo_full_name=repo_full_name,
        head_sha=head_sha,
        intent_id=intent_id,
        decision="pending",
        installation_id=event_installation_id,
        db_path=db_path,
    )

    return {"ok": True, "intent_id": intent_id, "action": "created"}


# ---------------------------------------------------------------------------
# PR closed (merged or just closed)
# ---------------------------------------------------------------------------

async def _handle_pr_closed(
    db_path: str,
    pr: dict[str, Any],
    intent_id: str,
    repo_full_name: str,
) -> dict[str, Any]:
    """Update intent when PR is closed."""
    merged = pr.get("merged", False)
    head_sha = pr.get("head", {}).get("sha", "")
    merge_commit = pr.get("merge_commit_sha", "")

    intent = event_log.get_intent(db_path, intent_id)
    if intent is None:
        log.warning("PR closed but no intent found: %s — check-run will not be updated", intent_id)
        return {"ok": True, "intent_id": intent_id, "action": "ignored", "reason": "unknown_intent"}

    if merged:
        new_status = Status.MERGED
        event_type = EventType.INTENT_MERGED
        decision = "merged"
    else:
        new_status = Status.REJECTED
        event_type = EventType.INTENT_REJECTED
        decision = "rejected"

    event_log.update_intent_status(db_path, intent_id, new_status)
    event_log.append(db_path, Event(
        event_type=event_type,
        intent_id=intent_id,
        tenant_id=intent.tenant_id,
        payload={
            "source": intent.source,
            "target": intent.target,
            "merged": merged,
            "merge_commit_sha": merge_commit,
            "trigger": "github_pr_closed",
        },
    ))

    stored_installation_id = intent.technical.get("installation_id")
    await try_publish_decision(
        repo_full_name=repo_full_name,
        head_sha=head_sha,
        intent_id=intent_id,
        decision=decision,
        reason="PR closed" if not merged else "PR merged",
        installation_id=stored_installation_id,
        db_path=db_path,
    )

    return {"ok": True, "intent_id": intent_id, "action": decision}


# ---------------------------------------------------------------------------
# Push → revalidation
# ---------------------------------------------------------------------------

async def _handle_push(
    db_path: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Handle push events: if the pushed branch is a source for an open intent,
    reset intent to READY for revalidation."""
    ref = data.get("ref", "")  # e.g. "refs/heads/feature/x"
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ""
    if not branch:
        return {"ok": True, "action": "ignored", "reason": "not_branch_push"}

    repo_full_name = data.get("repository", {}).get("full_name", "")
    head_sha = data.get("after", "")

    revalidated = []
    for status_val in (Status.READY.value, Status.VALIDATED.value):
        intents = event_log.list_intents(db_path, status=status_val, source=branch)
        for intent in intents:
            intent_repo = intent.technical.get("repo", "")
            if not intent_repo or intent_repo == repo_full_name:
                intent.technical["initial_base_commit"] = head_sha
                event_log.upsert_intent(db_path, intent)
                if intent.status != Status.READY:
                    event_log.update_intent_status(db_path, intent.id, Status.READY)
                event_log.append(db_path, Event(
                    event_type=EventType.INTENT_REQUEUED,
                    intent_id=intent.id,
                    tenant_id=intent.tenant_id,
                    payload={
                        "trigger": "push_revalidation",
                        "branch": branch,
                        "new_head_sha": head_sha,
                    },
                ))
                revalidated.append(intent.id)

                await try_publish_decision(
                    repo_full_name=repo_full_name,
                    head_sha=head_sha,
                    intent_id=intent.id,
                    decision="pending",
                    reason="Re-push detected, revalidating",
                    installation_id=intent.technical.get("installation_id"),
                    db_path=db_path,
                )

    return {"ok": True, "action": "push_processed", "revalidated": revalidated}


# ---------------------------------------------------------------------------
# merge_group → GitHub Merge Queue
# ---------------------------------------------------------------------------

async def _handle_merge_group(
    db_path: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Route merge_group actions to the appropriate handler."""
    action = data.get("action", "")
    merge_group = data.get("merge_group", {})
    repo_full_name = data.get("repository", {}).get("full_name", "")
    head_sha = merge_group.get("head_sha", "")

    if not head_sha or not repo_full_name:
        return {"ok": True, "action": "ignored", "reason": "incomplete_payload"}

    intent_id = f"{repo_full_name}:mg-{head_sha[:_SHA_DISPLAY_LEN]}"

    if action == "checks_requested":
        return await _handle_merge_group_checks_requested(
            db_path, data, merge_group, intent_id, repo_full_name, head_sha,
        )

    if action == "destroyed":
        return await _handle_merge_group_destroyed(
            db_path, data, merge_group, intent_id, repo_full_name,
        )

    return {"ok": True, "action": "ignored", "reason": f"unknown_merge_group_action_{action}"}


async def _handle_merge_group_checks_requested(
    db_path: str,
    data: dict[str, Any],
    merge_group: dict[str, Any],
    intent_id: str,
    repo_full_name: str,
    head_sha: str,
) -> dict[str, Any]:
    """Create intent when a PR enters GitHub's merge queue."""
    base_ref = merge_group.get("base_ref", "main")
    if base_ref.startswith("refs/heads/"):
        base_ref = base_ref[len("refs/heads/"):]
    head_ref = merge_group.get("head_ref", "")
    tenant = os.environ.get("CONVERGE_GITHUB_DEFAULT_TENANT")

    intent = Intent(
        id=intent_id,
        source=head_ref,
        target=base_ref,
        status=Status.READY,
        created_by="github-merge-queue",
        tenant_id=tenant,
        semantic={
            "problem_statement": "Merge queue candidate",
            "objective": "Validate merge group before integration",
        },
        technical={
            "source_ref": head_ref,
            "target_ref": base_ref,
            "initial_base_commit": head_sha,
            "repo": repo_full_name,
            "merge_group_head_ref": head_ref,
            "installation_id": data.get("installation", {}).get("id"),
            "webhook_event": "merge_group",
        },
    )
    event_log.upsert_intent(db_path, intent)
    event_log.append(db_path, Event(
        event_type=EventType.MERGE_GROUP_CHECKS_REQUESTED,
        intent_id=intent.id,
        tenant_id=tenant,
        payload=intent.to_dict(),
    ))

    event_installation_id = data.get("installation", {}).get("id")
    await try_publish_decision(
        repo_full_name=repo_full_name,
        head_sha=head_sha,
        intent_id=intent_id,
        decision="pending",
        reason="Merge queue entry — validation starting",
        installation_id=event_installation_id,
        db_path=db_path,
    )

    return {"ok": True, "intent_id": intent_id, "action": "merge_group_checks_requested"}


async def _handle_merge_group_destroyed(
    db_path: str,
    data: dict[str, Any],
    merge_group: dict[str, Any],
    intent_id: str,
    repo_full_name: str,
) -> dict[str, Any]:
    """Handle merge group destruction (dequeued, checks failed, or conflict)."""
    intent = event_log.get_intent(db_path, intent_id)
    if intent is None:
        return {"ok": True, "intent_id": intent_id, "action": "ignored", "reason": "unknown_intent"}

    reason = data.get("reason", "destroyed")

    event_log.update_intent_status(db_path, intent_id, Status.REJECTED)
    event_log.append(db_path, Event(
        event_type=EventType.MERGE_GROUP_DESTROYED,
        intent_id=intent_id,
        tenant_id=intent.tenant_id,
        payload={
            "source": intent.source,
            "target": intent.target,
            "reason": reason,
            "trigger": "github_merge_group_destroyed",
        },
    ))

    head_sha = merge_group.get("head_sha", "")
    await try_publish_decision(
        repo_full_name=repo_full_name,
        head_sha=head_sha,
        intent_id=intent_id,
        decision="rejected",
        reason=f"Merge group destroyed: {reason}",
        installation_id=intent.technical.get("installation_id"),
        db_path=db_path,
    )

    return {"ok": True, "intent_id": intent_id, "action": "merge_group_destroyed"}
