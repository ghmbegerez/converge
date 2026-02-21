"""GitHub App integration: JWT auth, installation tokens, check-run publishing.

Env vars: CONVERGE_GITHUB_APP_ID, CONVERGE_GITHUB_APP_PRIVATE_KEY_PATH,
CONVERGE_GITHUB_APP_PRIVATE_KEY, CONVERGE_GITHUB_API_URL.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import jwt  # PyJWT

log = logging.getLogger("converge.github")

# --- Configuration ---

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


# --- Shared helpers ---

def _safe_int(value: Any) -> int:
    """Convert *value* to a positive int, returning 0 on failure or non-positive."""
    try:
        n = int(value)
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


def is_configured() -> bool:
    """True when GitHub App integration is configured (APP_ID env var set)."""
    return bool(os.environ.get("CONVERGE_GITHUB_APP_ID"))


def resolve_installation_id(
    per_intent_value: Any = None,
    fallback_value: Any = None,
) -> int:
    """Resolve installation ID with priority: per-intent > fallback > env var.

    Returns a positive int, or 0 if no valid value found.
    """
    if per_intent_value not in (None, ""):
        n = _safe_int(per_intent_value)
        if n > 0:
            return n
    if fallback_value not in (None, ""):
        n = _safe_int(fallback_value)
        if n > 0:
            return n
    return _safe_int(os.environ.get("CONVERGE_GITHUB_INSTALLATION_ID", ""))


@asynccontextmanager
async def _ensure_client(client: httpx.AsyncClient | None):
    """Yield *client* as-is, or create a temporary ``AsyncClient``."""
    if client is not None:
        yield client
    else:
        async with httpx.AsyncClient() as c:
            yield c


# --- JWT ---

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


_GITHUB_HEADERS_ACCEPT = "application/vnd.github+json"

# --- Token cache ---

_token_cache: dict[int, tuple[str, float]] = {}  # installation_id -> (token, expires_at)


async def get_installation_token(
    installation_id: int,
    client: httpx.AsyncClient,
    *,
    app_id: str | None = None,
    private_key: str | None = None,
) -> str:
    """Get (or refresh) an installation access token."""
    cached = _token_cache.get(installation_id)
    if cached and cached[1] > time.time() + _TOKEN_REFRESH_MARGIN:
        return cached[0]

    token_jwt = generate_jwt(app_id=app_id, private_key=private_key)
    url = f"{_api_url()}/app/installations/{installation_id}/access_tokens"
    resp = await client.post(
        url,
        headers={
            "Authorization": f"Bearer {token_jwt}",
            "Accept": _GITHUB_HEADERS_ACCEPT,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["token"]
    _token_cache[installation_id] = (token, time.time() + 3500)
    return token


def reset_token_cache() -> None:
    """Clear token cache (for tests)."""
    _token_cache.clear()


# --- Internal API helpers ---


async def _post_check_run(
    client: httpx.AsyncClient,
    token: str,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    name: str = "Converge Risk Gate",
    status: str = "queued",
    conclusion: str | None = None,
    summary: str = "",
    details_url: str | None = None,
) -> dict[str, Any]:
    """POST a check-run. Client and token provided by caller."""
    url = f"{_api_url()}/repos/{owner}/{repo}/check-runs"
    body: dict[str, Any] = {
        "name": name,
        "head_sha": head_sha,
        "status": status,
    }
    if conclusion:
        body["conclusion"] = conclusion
    if summary:
        body["output"] = {"title": name, "summary": summary}
    if details_url:
        body["details_url"] = details_url
    resp = await client.post(
        url,
        json=body,
        headers={"Authorization": f"token {token}", "Accept": _GITHUB_HEADERS_ACCEPT},
    )
    resp.raise_for_status()
    return resp.json()


async def _post_commit_status(
    client: httpx.AsyncClient,
    token: str,
    *,
    owner: str,
    repo: str,
    sha: str,
    state: str,
    context: str = "converge/risk-gate",
    description: str = "",
    target_url: str | None = None,
) -> dict[str, Any]:
    """POST a commit status. Client and token provided by caller."""
    url = f"{_api_url()}/repos/{owner}/{repo}/statuses/{sha}"
    body: dict[str, Any] = {
        "state": state,
        "context": context,
    }
    if description:
        body["description"] = description[:140]  # GitHub limit
    if target_url:
        body["target_url"] = target_url
    resp = await client.post(
        url,
        json=body,
        headers={"Authorization": f"token {token}", "Accept": _GITHUB_HEADERS_ACCEPT},
    )
    resp.raise_for_status()
    return resp.json()


# --- Publish mode ---

_VALID_PUBLISH_MODES = {"checks", "status", "both"}


def _publish_mode() -> str:
    """Return the configured publish mode (checks | status | both)."""
    mode = os.environ.get("CONVERGE_GITHUB_PUBLISH_MODE", "both").lower()
    if mode not in _VALID_PUBLISH_MODES:
        log.warning("Invalid CONVERGE_GITHUB_PUBLISH_MODE=%r — defaulting to 'both'", mode)
        return "both"
    return mode


# Decision → (check-run status, conclusion, commit status state)
_DECISION_MAP: dict[str, tuple[str, str | None, str]] = {
    "validated": ("completed", "success", "success"),
    "merged":    ("completed", "success", "success"),
    "blocked":   ("completed", "failure", "failure"),
    "rejected":  ("completed", "failure", "failure"),
    "pending":   ("in_progress", None, "pending"),
}


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
) -> dict[str, Any]:
    """Publish a Converge decision as check-run, commit status, or both.

    Raises on failure — callers handle errors.
    """
    if decision not in _DECISION_MAP:
        log.warning("Unknown decision %r for %s — publishing as pending", decision, intent_id)
    cr_status, cr_conclusion, commit_state = _DECISION_MAP.get(
        decision, ("in_progress", None, "pending"),
    )

    if decision == "validated":
        desc = f"Validated (risk={risk_score:.1f})"
    elif decision == "merged":
        desc = "Merged"
    elif decision in ("blocked", "rejected"):
        desc = reason[:140] if reason else f"Blocked (risk={risk_score:.1f})"
    else:
        desc = reason[:140] if reason else "Processing..."

    summary_parts = [
        f"**Intent:** `{intent_id}`",
        f"**Decision:** {decision}",
        f"**Risk score:** {risk_score:.1f}",
    ]
    if trace_id:
        summary_parts.append(f"**Trace:** `{trace_id}`")
    if reason:
        summary_parts.append(f"**Reason:** {reason}")

    mode = _publish_mode()
    result: dict[str, Any] = {"decision": decision}

    async with _ensure_client(client) as c:
        token = await get_installation_token(installation_id, c)
        if mode in ("checks", "both"):
            check_run = await _post_check_run(
                c, token,
                owner=owner, repo=repo, head_sha=head_sha,
                status=cr_status, conclusion=cr_conclusion,
                summary="\n".join(summary_parts),
            )
            result["check_run_id"] = check_run.get("id")

        if mode in ("status", "both"):
            await _post_commit_status(
                c, token,
                owner=owner, repo=repo, sha=head_sha,
                state=commit_state, description=desc,
            )
            result["commit_status_state"] = commit_state

        return result
