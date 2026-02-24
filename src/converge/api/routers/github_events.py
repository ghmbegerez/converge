"""GitHub webhook domain-logic handlers.

Extracted from webhooks.py so the HTTP route keeps only infrastructure
(signature verification, idempotency, parsing) while all event-handling
logic lives here.

Public entry point: :func:`dispatch_github_event`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from converge import event_log
from converge.integrations.github_publish import try_publish_decision
from converge.models import Event, EventType, Intent, Status

log = logging.getLogger("converge.github_events")

# --- Display constants ---
_SHA_DISPLAY_LEN = 12  # characters of SHA shown in intent IDs


# ---------------------------------------------------------------------------
# De-duplication helpers
# ---------------------------------------------------------------------------

def _build_pr_intent(
    pr: dict[str, Any],
    data: dict[str, Any],
    intent_id: str,
    repo_full_name: str,
) -> Intent | None:
    """Build an Intent from a PR or merge-group payload.

    Returns *None* when the payload lacks the minimum required fields
    (head SHA or source ref for PR events).
    """
    # --- PR path ---
    head_info = pr.get("head", {})
    base_info = pr.get("base", {})
    source = head_info.get("ref", "")
    target = base_info.get("ref", "main")
    head_sha = head_info.get("sha", "")

    if not head_sha or not source:
        return None

    tenant = os.environ.get("CONVERGE_GITHUB_DEFAULT_TENANT")

    return Intent(
        id=intent_id,
        source=source,
        target=target,
        status=Status.READY,
        created_by="github-webhook",
        tenant_id=tenant,
        origin_type="integration",
        semantic={
            "problem_statement": pr.get("title", ""),
            "objective": pr.get("title", ""),
        },
        technical={
            "source_ref": source,
            "target_ref": target,
            "initial_base_commit": head_sha,
            "repo": repo_full_name,
            "pr_number": pr.get("number", 0),
            "installation_id": data.get("installation", {}).get("id"),
        },
    )


def _record_commit_link(
    intent_id: str,
    repo: str,
    sha: str,
    role: str,
    trigger: str,
    tenant_id: str | None = None,
) -> None:
    """Persist a commit link and emit the corresponding audit event."""
    event_log.upsert_commit_link(intent_id, repo, sha, role)
    event_log.append(Event(
        event_type=EventType.INTENT_LINKED_COMMIT,
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload={"repo": repo, "sha": sha, "role": role, "trigger": trigger},
    ))


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

async def dispatch_github_event(
    event_type: str,
    data: dict[str, Any],
    delivery_id: str,
) -> dict[str, Any]:
    """Route a parsed GitHub event to the appropriate domain handler.

    Returns a JSON-serialisable dict that becomes the HTTP response body.
    """

    # ---------------------------------------------------------------
    # pull_request events
    # ---------------------------------------------------------------
    if event_type == "pull_request":
        action = data.get("action", "")
        pr = data.get("pull_request", {})
        pr_number = pr.get("number", 0)
        repo_full_name = data.get("repository", {}).get("full_name", "")
        intent_id = (
            f"{repo_full_name}:pr-{pr_number}"
            if repo_full_name
            else f"pr-{pr_number}"
        )

        if action in ("opened", "synchronize", "reopened"):
            return await _handle_pr_opened(data, pr, intent_id, repo_full_name)

        if action == "closed":
            return await _handle_pr_closed(pr, intent_id, repo_full_name)

    # ---------------------------------------------------------------
    # push events -> revalidate if branch matches an open intent
    # ---------------------------------------------------------------
    if event_type == "push":
        return await _handle_push(data)

    # ---------------------------------------------------------------
    # merge_group events -> GitHub Merge Queue integration
    # ---------------------------------------------------------------
    if event_type == "merge_group":
        return await _handle_merge_group(data)

    return {"ok": True, "delivery_id": delivery_id}


# ---------------------------------------------------------------------------
# PR opened / synchronize / reopened
# ---------------------------------------------------------------------------

async def _handle_pr_opened(
    data: dict[str, Any],
    pr: dict[str, Any],
    intent_id: str,
    repo_full_name: str,
) -> dict[str, Any]:
    """Create or update intent from PR, set status to READY."""
    intent = _build_pr_intent(pr, data, intent_id, repo_full_name)
    if intent is None:
        return {
            "ok": True,
            "intent_id": intent_id,
            "action": "ignored",
            "reason": "missing_head_sha_or_ref",
        }

    head_sha = pr.get("head", {}).get("sha", "")

    # Intake pre-check: evaluate system health before accepting
    from converge.intake import evaluate_intake
    intake_decision = evaluate_intake(intent)
    if not intake_decision.accepted:
        return {
            "ok": True, "intent_id": intent_id, "action": "intake_rejected",
            "mode": intake_decision.mode.value, "reason": intake_decision.reason,
        }

    event_log.upsert_intent(intent)
    event_log.append(Event(
        event_type=EventType.INTENT_CREATED,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=intent.to_dict(),
    ))

    # AR-04: persist explicit commit link
    _record_commit_link(intent_id, repo_full_name, head_sha, "head", "pr_opened",
                        tenant_id=intent.tenant_id)

    event_installation_id = data.get("installation", {}).get("id")
    await try_publish_decision(
        repo_full_name=repo_full_name,
        head_sha=head_sha,
        intent_id=intent_id,
        decision="pending",
        installation_id=event_installation_id,
    )

    return {"ok": True, "intent_id": intent_id, "action": "created"}


# ---------------------------------------------------------------------------
# PR closed (merged or just closed)
# ---------------------------------------------------------------------------

async def _handle_pr_closed(
    pr: dict[str, Any],
    intent_id: str,
    repo_full_name: str,
) -> dict[str, Any]:
    """Update intent when PR is closed."""
    merged = pr.get("merged", False)
    head_sha = pr.get("head", {}).get("sha", "")
    merge_commit = pr.get("merge_commit_sha", "")

    intent = event_log.get_intent(intent_id)
    if intent is None:
        log.warning("PR closed but no intent found: %s -- check-run will not be updated", intent_id)
        return {"ok": True, "intent_id": intent_id, "action": "ignored", "reason": "unknown_intent"}

    if merged:
        new_status = Status.MERGED
        evt_type = EventType.INTENT_MERGED
        decision = "merged"
    else:
        new_status = Status.REJECTED
        evt_type = EventType.INTENT_REJECTED
        decision = "rejected"

    event_log.update_intent_status(intent_id, new_status)
    event_log.append(Event(
        event_type=evt_type,
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

    # AR-04: persist merge commit link
    if merged and merge_commit:
        intent_repo = intent.technical.get("repo", repo_full_name)
        _record_commit_link(intent_id, intent_repo, merge_commit, "merge", "pr_merged",
                            tenant_id=intent.tenant_id)

    stored_installation_id = intent.technical.get("installation_id")
    await try_publish_decision(
        repo_full_name=repo_full_name,
        head_sha=head_sha,
        intent_id=intent_id,
        decision=decision,
        reason="PR closed" if not merged else "PR merged",
        installation_id=stored_installation_id,
    )

    return {"ok": True, "intent_id": intent_id, "action": decision}


# ---------------------------------------------------------------------------
# Push -> revalidation
# ---------------------------------------------------------------------------

async def _handle_push(
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
        intents = event_log.list_intents(status=status_val, source=branch)
        for intent in intents:
            intent_repo = intent.technical.get("repo", "")
            if not intent_repo or intent_repo == repo_full_name:
                intent.technical["initial_base_commit"] = head_sha
                event_log.upsert_intent(intent)
                if intent.status != Status.READY:
                    event_log.update_intent_status(intent.id, Status.READY)
                event_log.append(Event(
                    event_type=EventType.INTENT_REQUEUED,
                    intent_id=intent.id,
                    tenant_id=intent.tenant_id,
                    payload={
                        "trigger": "push_revalidation",
                        "branch": branch,
                        "new_head_sha": head_sha,
                    },
                ))
                # AR-04: update head commit link
                event_log.upsert_commit_link(intent.id, repo_full_name, head_sha, "head")
                revalidated.append(intent.id)

                await try_publish_decision(
                    repo_full_name=repo_full_name,
                    head_sha=head_sha,
                    intent_id=intent.id,
                    decision="pending",
                    reason="Re-push detected, revalidating",
                    installation_id=intent.technical.get("installation_id"),
                )

    return {"ok": True, "action": "push_processed", "revalidated": revalidated}


# ---------------------------------------------------------------------------
# merge_group -> GitHub Merge Queue
# ---------------------------------------------------------------------------

async def _handle_merge_group(
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
            data, merge_group, intent_id, repo_full_name, head_sha,
        )

    if action == "destroyed":
        return await _handle_merge_group_destroyed(
            data, merge_group, intent_id, repo_full_name,
        )

    return {"ok": True, "action": "ignored", "reason": f"unknown_merge_group_action_{action}"}


async def _handle_merge_group_checks_requested(
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
        origin_type="integration",
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

    # Intake pre-check: evaluate system health before accepting
    from converge.intake import evaluate_intake
    intake_decision = evaluate_intake(intent)
    if not intake_decision.accepted:
        return {
            "ok": True, "intent_id": intent_id, "action": "intake_rejected",
            "mode": intake_decision.mode.value, "reason": intake_decision.reason,
        }

    event_log.upsert_intent(intent)
    event_log.append(Event(
        event_type=EventType.MERGE_GROUP_CHECKS_REQUESTED,
        intent_id=intent.id,
        tenant_id=tenant,
        payload=intent.to_dict(),
    ))

    # AR-04: persist head commit link for merge group
    _record_commit_link(intent_id, repo_full_name, head_sha, "head", "merge_group",
                        tenant_id=tenant)

    event_installation_id = data.get("installation", {}).get("id")
    await try_publish_decision(
        repo_full_name=repo_full_name,
        head_sha=head_sha,
        intent_id=intent_id,
        decision="pending",
        reason="Merge queue entry -- validation starting",
        installation_id=event_installation_id,
    )

    return {"ok": True, "intent_id": intent_id, "action": "merge_group_checks_requested"}


async def _handle_merge_group_destroyed(
    data: dict[str, Any],
    merge_group: dict[str, Any],
    intent_id: str,
    repo_full_name: str,
) -> dict[str, Any]:
    """Handle merge group destruction (dequeued, checks failed, or conflict)."""
    intent = event_log.get_intent(intent_id)
    if intent is None:
        return {"ok": True, "intent_id": intent_id, "action": "ignored", "reason": "unknown_intent"}

    reason = data.get("reason", "destroyed")

    event_log.update_intent_status(intent_id, Status.REJECTED)
    event_log.append(Event(
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
    )

    return {"ok": True, "intent_id": intent_id, "action": "merge_group_destroyed"}
