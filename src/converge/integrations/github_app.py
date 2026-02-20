"""GitHub App integration: JWT auth, installation tokens, check-run publishing.

This is the **only** async module in Converge. All GitHub API calls use
``httpx.AsyncClient`` so they can be awaited from the webhook handler
or called via ``asyncio.run()`` from the synchronous worker.

Configuration via environment variables:
  CONVERGE_GITHUB_APP_ID           — numeric app ID
  CONVERGE_GITHUB_APP_PRIVATE_KEY_PATH — path to PEM private key file
  CONVERGE_GITHUB_APP_PRIVATE_KEY  — PEM contents (fallback, for tests)
  CONVERGE_GITHUB_API_URL          — base URL (default https://api.github.com)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
import jwt  # PyJWT

log = logging.getLogger("converge.github")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_API_URL = "https://api.github.com"
_JWT_ALGORITHM = "RS256"
_JWT_EXPIRY_SECONDS = 600  # 10 min (GitHub max)
_TOKEN_REFRESH_MARGIN = 60  # refresh 60s before expiry


def _get_private_key() -> str:
    """Load the GitHub App private key from file or env var."""
    path = os.environ.get("CONVERGE_GITHUB_APP_PRIVATE_KEY_PATH", "")
    if path and os.path.isfile(path):
        with open(path) as f:
            return f.read()
    raw = os.environ.get("CONVERGE_GITHUB_APP_PRIVATE_KEY", "")
    if raw:
        return raw
    raise RuntimeError(
        "GitHub App private key not configured. "
        "Set CONVERGE_GITHUB_APP_PRIVATE_KEY_PATH or CONVERGE_GITHUB_APP_PRIVATE_KEY."
    )


def _get_app_id() -> str:
    app_id = os.environ.get("CONVERGE_GITHUB_APP_ID", "")
    if not app_id:
        raise RuntimeError("CONVERGE_GITHUB_APP_ID not set.")
    return app_id


def _api_url() -> str:
    return os.environ.get("CONVERGE_GITHUB_API_URL", _DEFAULT_API_URL).rstrip("/")


# ---------------------------------------------------------------------------
# JWT generation
# ---------------------------------------------------------------------------

def generate_jwt(app_id: str | None = None, private_key: str | None = None) -> str:
    """Create a short-lived JWT for authenticating as the GitHub App."""
    aid = app_id or _get_app_id()
    key = private_key or _get_private_key()
    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued-at (60s clock skew allowance)
        "exp": now + _JWT_EXPIRY_SECONDS,
        "iss": aid,
    }
    return jwt.encode(payload, key, algorithm=_JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# Installation token cache
# ---------------------------------------------------------------------------

_token_cache: dict[int, tuple[str, float]] = {}  # installation_id → (token, expires_at)


async def get_installation_token(
    installation_id: int,
    *,
    client: httpx.AsyncClient | None = None,
    app_id: str | None = None,
    private_key: str | None = None,
) -> str:
    """Get (or refresh) an installation access token.

    Tokens are cached and reused until they're close to expiry.
    """
    cached = _token_cache.get(installation_id)
    if cached and cached[1] > time.time() + _TOKEN_REFRESH_MARGIN:
        return cached[0]

    token_jwt = generate_jwt(app_id=app_id, private_key=private_key)
    url = f"{_api_url()}/app/installations/{installation_id}/access_tokens"

    should_close = client is None
    client = client or httpx.AsyncClient()
    try:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token_jwt}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["token"]
        # GitHub tokens expire in 1 hour; parse if provided, else assume 3600s
        expires_at = time.time() + 3500  # ~58 min safe margin
        _token_cache[installation_id] = (token, expires_at)
        return token
    finally:
        if should_close:
            await client.aclose()


def reset_token_cache() -> None:
    """Clear token cache (for tests)."""
    _token_cache.clear()


# ---------------------------------------------------------------------------
# Check-run publishing
# ---------------------------------------------------------------------------

async def create_check_run(
    *,
    owner: str,
    repo: str,
    installation_id: int,
    head_sha: str,
    name: str = "Converge Risk Gate",
    status: str = "queued",
    conclusion: str | None = None,
    summary: str = "",
    details_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    app_id: str | None = None,
    private_key: str | None = None,
) -> dict[str, Any]:
    """Create a GitHub check-run on a commit.

    ``status``: queued | in_progress | completed
    ``conclusion`` (required when completed): success | failure | neutral | cancelled
    """
    token = await get_installation_token(
        installation_id, client=client, app_id=app_id, private_key=private_key,
    )
    url = f"{_api_url()}/repos/{owner}/{repo}/check-runs"

    body: dict[str, Any] = {
        "name": name,
        "head_sha": head_sha,
        "status": status,
    }
    if conclusion:
        body["conclusion"] = conclusion
    if summary:
        body["output"] = {
            "title": name,
            "summary": summary,
        }
    if details_url:
        body["details_url"] = details_url

    should_close = client is None
    client = client or httpx.AsyncClient()
    try:
        resp = await client.post(
            url,
            json=body,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        return resp.json()
    finally:
        if should_close:
            await client.aclose()


async def update_check_run(
    *,
    owner: str,
    repo: str,
    installation_id: int,
    check_run_id: int,
    status: str | None = None,
    conclusion: str | None = None,
    summary: str | None = None,
    client: httpx.AsyncClient | None = None,
    app_id: str | None = None,
    private_key: str | None = None,
) -> dict[str, Any]:
    """Update an existing check-run (e.g. mark completed)."""
    token = await get_installation_token(
        installation_id, client=client, app_id=app_id, private_key=private_key,
    )
    url = f"{_api_url()}/repos/{owner}/{repo}/check-runs/{check_run_id}"

    body: dict[str, Any] = {}
    if status:
        body["status"] = status
    if conclusion:
        body["conclusion"] = conclusion
    if summary:
        body["output"] = {
            "title": "Converge Risk Gate",
            "summary": summary,
        }

    should_close = client is None
    client = client or httpx.AsyncClient()
    try:
        resp = await client.patch(
            url,
            json=body,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        return resp.json()
    finally:
        if should_close:
            await client.aclose()


# ---------------------------------------------------------------------------
# Commit status publishing
# ---------------------------------------------------------------------------

async def create_commit_status(
    *,
    owner: str,
    repo: str,
    installation_id: int,
    sha: str,
    state: str,
    context: str = "converge/risk-gate",
    description: str = "",
    target_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    app_id: str | None = None,
    private_key: str | None = None,
) -> dict[str, Any]:
    """Create a commit status on a PR.

    ``state``: pending | success | failure | error
    """
    token = await get_installation_token(
        installation_id, client=client, app_id=app_id, private_key=private_key,
    )
    url = f"{_api_url()}/repos/{owner}/{repo}/statuses/{sha}"

    body: dict[str, Any] = {
        "state": state,
        "context": context,
    }
    if description:
        body["description"] = description[:140]  # GitHub limit
    if target_url:
        body["target_url"] = target_url

    should_close = client is None
    client = client or httpx.AsyncClient()
    try:
        resp = await client.post(
            url,
            json=body,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        return resp.json()
    finally:
        if should_close:
            await client.aclose()


# ---------------------------------------------------------------------------
# High-level: publish decision to GitHub
# ---------------------------------------------------------------------------

async def publish_decision(
    *,
    owner: str,
    repo: str,
    installation_id: int,
    head_sha: str,
    intent_id: str,
    decision: str,
    trace_id: str = "",
    risk_score: float = 0.0,
    reason: str = "",
    client: httpx.AsyncClient | None = None,
    app_id: str | None = None,
    private_key: str | None = None,
) -> dict[str, Any]:
    """Publish a Converge decision as both a check-run and commit status.

    Called after validate_intent or process_queue decides on an intent.
    """
    # Map decision → check-run status/conclusion + commit state
    if decision == "validated":
        cr_status, cr_conclusion = "completed", "success"
        commit_state = "success"
        desc = f"Validated (risk={risk_score:.1f})"
    elif decision in ("blocked", "rejected"):
        cr_status, cr_conclusion = "completed", "failure"
        commit_state = "failure"
        desc = reason[:140] if reason else f"Blocked (risk={risk_score:.1f})"
    else:
        cr_status, cr_conclusion = "in_progress", None
        commit_state = "pending"
        desc = "Processing..."

    summary_lines = [
        f"**Intent:** `{intent_id}`",
        f"**Decision:** {decision}",
        f"**Risk score:** {risk_score:.1f}",
    ]
    if trace_id:
        summary_lines.append(f"**Trace:** `{trace_id}`")
    if reason:
        summary_lines.append(f"**Reason:** {reason}")
    summary = "\n".join(summary_lines)

    should_close = client is None
    client = client or httpx.AsyncClient()
    try:
        # Create check-run
        check_run = await create_check_run(
            owner=owner,
            repo=repo,
            installation_id=installation_id,
            head_sha=head_sha,
            status=cr_status,
            conclusion=cr_conclusion,
            summary=summary,
            client=client,
            app_id=app_id,
            private_key=private_key,
        )

        # Create commit status
        commit_status = await create_commit_status(
            owner=owner,
            repo=repo,
            installation_id=installation_id,
            sha=head_sha,
            state=commit_state,
            description=desc,
            client=client,
            app_id=app_id,
            private_key=private_key,
        )

        return {
            "check_run_id": check_run.get("id"),
            "commit_status_state": commit_state,
            "decision": decision,
        }
    except Exception:
        log.exception("Failed to publish decision to GitHub for %s", intent_id)
        return {"error": "publish_failed", "decision": decision}
    finally:
        if should_close:
            await client.aclose()
