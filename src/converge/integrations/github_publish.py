"""GitHub decision publishing facade.

Single entry point for publishing Converge decisions to GitHub.
All callers (webhooks, worker, CLI) go through ``try_publish_decision``.
Internal details (JWT, tokens, API calls) are encapsulated in ``github_app``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from converge import event_log
from converge.models import Event, EventType

log = logging.getLogger("converge.github.publish")


async def try_publish_decision(
    repo_full_name: str,
    head_sha: str,
    intent_id: str,
    decision: str,
    trace_id: str = "",
    risk_score: float = 0.0,
    reason: str = "",
    installation_id: int | None = None,
    fallback_installation_id: Any = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Best-effort publish a decision to GitHub. Never raises.

    ``installation_id`` — prefer the value stored in the intent (from the
    webhook event).  ``fallback_installation_id`` — secondary override
    (e.g. worker config).  Falls back to the global ENV var.

    ``client`` — if provided, reuses the caller's ``AsyncClient`` (e.g. the
    worker passes a shared client across a batch).  Otherwise creates a
    one-shot client per call.

    on success or ``GITHUB_DECISION_PUBLISH_FAILED`` on failure.
    """
    from converge.integrations.github_app import (
        is_configured,
        publish_decision,
        resolve_installation_id,
    )

    if not is_configured():
        return
    try:
        parts = repo_full_name.split("/", 1)
        if len(parts) != 2:
            return
        owner, repo = parts
        resolved_id = resolve_installation_id(installation_id, fallback_installation_id)
        if not resolved_id:
            log.warning(
                "No valid installation_id for %s — skipping GitHub publish",
                intent_id,
            )
            return

        result = await publish_decision(
            owner=owner,
            repo=repo,
            installation_id=resolved_id,
            head_sha=head_sha,
            intent_id=intent_id,
            decision=decision,
            trace_id=trace_id,
            risk_score=risk_score,
            reason=reason,
            client=client,
        )

        event_log.append(Event(
                event_type=EventType.GITHUB_DECISION_PUBLISHED,
                intent_id=intent_id,
                payload={
                    "decision": decision,
                    "head_sha": head_sha,
                    "repo": repo_full_name,
                    "check_run_id": result.get("check_run_id"),
                },
            ))
    except Exception as exc:
        log.warning("Failed to publish decision to GitHub for %s", intent_id, exc_info=True)
        try:
            event_log.append(Event(
                event_type=EventType.GITHUB_DECISION_PUBLISH_FAILED,
                intent_id=intent_id,
                payload={
                    "decision": decision,
                    "head_sha": head_sha,
                    "repo": repo_full_name,
                    "error": str(exc),
                },
            ))
        except Exception:
            log.warning("Failed to record publish failure event for %s", intent_id)
