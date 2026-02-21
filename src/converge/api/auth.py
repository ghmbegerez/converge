"""Authentication, authorization, scopes, key rotation, and access auditing.

Provides both standalone functions (backward compat with unit tests)
and FastAPI dependency functions for the ASGI server.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request

log = logging.getLogger("converge.auth")

# --- Auth constants ---
_KEY_PREFIX_LEN = 4             # characters of API key shown in logs
_TOKEN_BYTES = 32               # bytes for generated API keys


# ---------------------------------------------------------------------------
# Role hierarchy
# ---------------------------------------------------------------------------

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}

API_ROLE_MAP: dict[str, str] = {
    "/api/auth/whoami": "viewer",
    "/api/summary": "viewer",
    "/api/intents": "viewer",
    "/api/metrics/integration": "viewer",
    "/api/policy/recent": "viewer",
    "/api/queue/state": "viewer",
    "/api/queue/summary": "viewer",
    "/api/compliance/report": "viewer",
    "/api/compliance/alerts": "viewer",
    "/api/compliance/thresholds": "viewer",
    "/api/health/repo/now": "viewer",
    "/api/health/repo/trend": "viewer",
    "/api/health/change": "viewer",
    "/api/health/change/trend": "viewer",
    "/api/health/entropy/trend": "viewer",
    "/api/risk/recent": "viewer",
    "/api/risk/review": "viewer",
    "/api/risk/shadow/recent": "viewer",
    "/api/risk/gate/report": "viewer",
    "/api/risk/policy": "viewer",
    "/api/impact/edges": "viewer",
    "/api/diagnostics/recent": "viewer",
    "/api/agent/policy": "viewer",
    "/api/events": "viewer",
    "/api/predictions": "viewer",
    "/api/dashboard": "viewer",
    "/api/dashboard/alerts": "viewer",
    "/api/export/decisions": "viewer",
    "/api/audit/recent": "operator",
    "/api/compliance/thresholds/history": "operator",
}


# ---------------------------------------------------------------------------
# Scope definitions
# ---------------------------------------------------------------------------

# Scopes: "resource.action" — `*` means all scopes.
SCOPE_MAP: dict[str, str] = {
    # Intents
    "GET /intents": "intents.read",
    "GET /summary": "intents.read",
    "GET /predictions": "intents.read",
    # Queue
    "GET /queue/state": "queue.read",
    "GET /queue/summary": "queue.read",
    # Risk
    "GET /risk/recent": "risk.read",
    "GET /risk/review": "risk.read",
    "GET /risk/shadow/recent": "risk.read",
    "GET /risk/gate/report": "risk.read",
    "GET /risk/policy": "risk.read",
    "POST /risk/policy": "risk.write",
    "GET /impact/edges": "risk.read",
    "GET /diagnostics/recent": "risk.read",
    # Agents
    "GET /agent/policy": "agents.read",
    "POST /agent/policy": "agents.write",
    "POST /agent/authorize": "agents.admin",
    # Compliance
    "GET /compliance/report": "compliance.read",
    "GET /compliance/alerts": "compliance.read",
    "GET /compliance/thresholds": "compliance.read",
    "POST /compliance/thresholds": "compliance.write",
    "GET /compliance/thresholds/history": "compliance.read",
    # Events
    "GET /events": "events.read",
    "GET /audit/recent": "events.read",
    "GET /policy/recent": "events.read",
    "GET /metrics/integration": "events.read",
    # Health projections
    "GET /health/repo/now": "intents.read",
    "GET /health/repo/trend": "intents.read",
    "GET /health/change": "intents.read",
    "GET /health/change/trend": "intents.read",
    "GET /health/entropy/trend": "intents.read",
    # Dashboard & Export
    "GET /dashboard": "intents.read",
    "GET /dashboard/alerts": "compliance.read",
    "GET /export/decisions": "events.read",
    # Auth
    "GET /auth/whoami": "intents.read",
    "POST /auth/keys/rotate": "admin",
}


def _resolve_scope(method: str, path: str) -> str | None:
    """Determine the required scope for a request.

    Strips /api or /v1 prefix before lookup.
    """
    for prefix in ("/api", "/v1"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    key = f"{method.upper()} {path}"
    return SCOPE_MAP.get(key)


def _principal_has_scope(principal: dict[str, Any], scope: str) -> bool:
    """Check if a principal's scopes include the required scope."""
    scopes_raw = principal.get("scopes")
    if scopes_raw is None:
        # No scopes defined → role alone determines access (backward compat)
        return True
    if scopes_raw == "*":
        return True
    allowed = {s.strip() for s in scopes_raw.split(",")}
    # Wildcard per resource: "risk.*" covers "risk.read" and "risk.write"
    resource = scope.split(".")[0] if "." in scope else scope
    if f"{resource}.*" in allowed:
        return True
    return scope in allowed


# ---------------------------------------------------------------------------
# Key rotation (in-process grace period)
# ---------------------------------------------------------------------------

# Maps hashed-old-key → {principal dict + "expires_at": float}
_rotated_keys: dict[str, dict[str, Any]] = {}


def _register_rotated_key(
    old_key_hash: str,
    principal: dict[str, Any],
    grace_seconds: int,
) -> None:
    """Mark an old key as still valid during the grace period."""
    _rotated_keys[old_key_hash] = {
        **principal,
        "_expires_at": time.time() + grace_seconds,
    }


def _check_rotated_key(hashed: str) -> dict[str, Any] | None:
    """Check rotated keys, cleaning up expired ones."""
    entry = _rotated_keys.get(hashed)
    if entry is None:
        return None
    if time.time() > entry["_expires_at"]:
        del _rotated_keys[hashed]
        return None
    # Return principal without internal fields
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def reset_rotated_keys() -> None:
    """Clear rotated keys (for tests)."""
    _rotated_keys.clear()


# ---------------------------------------------------------------------------
# Core helpers (also used by unit tests / unit tests)
# ---------------------------------------------------------------------------

def _parse_api_keys() -> dict[str, dict[str, str | None]]:
    """Parse CONVERGE_API_KEYS env var: key:role:actor[:tenant[:scopes]]"""
    raw = os.environ.get("CONVERGE_API_KEYS", "")
    if not raw:
        return {}
    keys: dict[str, dict[str, str | None]] = {}
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) >= 3:
            k, role, actor = parts[0], parts[1], parts[2]
            tenant = parts[3] if len(parts) > 3 else None
            scopes = parts[4] if len(parts) > 4 else None
            hashed = hashlib.sha256(k.encode()).hexdigest()
            keys[hashed] = {
                "role": role,
                "actor": actor,
                "tenant": tenant,
                "scopes": scopes,
                "key_prefix": k[:_KEY_PREFIX_LEN],
            }
    return keys


def _auth_required() -> bool:
    return os.environ.get("CONVERGE_AUTH_REQUIRED", "1") == "1"


def _authorize_request(headers: dict[str, str], path: str) -> dict[str, Any] | None:
    """Returns principal dict or None if unauthorized.

    Kept as a standalone function for backward compatibility with unit tests
    and unit tests.py.
    """
    if not _auth_required():
        return {"role": "admin", "actor": "anonymous", "tenant": None}

    api_key = headers.get("x-api-key", "")
    if api_key:
        hashed = hashlib.sha256(api_key.encode()).hexdigest()
        registry = _parse_api_keys()
        principal = registry.get(hashed)

        # Check rotated keys if not in primary registry
        if principal is None:
            principal = _check_rotated_key(hashed)

        if principal is None:
            return None
        required_role = API_ROLE_MAP.get(path, "admin")
        if ROLE_RANK.get(principal["role"], -1) < ROLE_RANK.get(required_role, 99):
            return None
        return principal

    return None


def _verify_github_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Access auditing
# ---------------------------------------------------------------------------

def _record_access_event(
    event_type: str,
    *,
    method: str = "",
    path: str = "",
    actor: str = "",
    role: str = "",
    tenant: str | None = None,
    reason: str = "",
    db_path: str = "",
) -> None:
    """Record an access.granted or access.denied event in the event log."""
    try:
        from converge import event_log
        from converge.models import Event

        event_log.append(db_path, Event(
            event_type=event_type,
            tenant_id=tenant,
            payload={
                "method": method,
                "path": path,
                "actor": actor,
                "role": role,
                "reason": reason,
            },
        ))
    except Exception:
        # Never let audit logging break the request
        log.debug("Failed to record access event", exc_info=True)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def _authenticate(api_key: str, method: str, path: str, db_path: str) -> dict[str, Any]:
    """Validate API key and return principal, or raise 401."""
    if not api_key:
        _record_access_event("access.denied", method=method, path=path,
                             reason="no_api_key", db_path=db_path)
        raise HTTPException(status_code=401, detail="Unauthorized")

    hashed = hashlib.sha256(api_key.encode()).hexdigest()
    principal = _parse_api_keys().get(hashed) or _check_rotated_key(hashed)

    if principal is None:
        _record_access_event("access.denied", method=method, path=path,
                             reason="invalid_key", db_path=db_path)
        raise HTTPException(status_code=401, detail="Unauthorized")
    return principal


def _authorize_role(
    principal: dict[str, Any], min_role: str,
    method: str, path: str, db_path: str,
) -> None:
    """Check role, raise 401 if insufficient."""
    if ROLE_RANK.get(principal["role"], -1) < ROLE_RANK.get(min_role, 99):
        _record_access_event(
            "access.denied", method=method, path=path,
            actor=principal.get("actor", ""), role=principal.get("role", ""),
            tenant=principal.get("tenant"), reason="insufficient_role",
            db_path=db_path,
        )
        raise HTTPException(status_code=401, detail="Unauthorized")


def _authorize_scope(
    principal: dict[str, Any],
    method: str, path: str, db_path: str,
) -> None:
    """Check scope, raise 403 if missing."""
    required_scope = _resolve_scope(method, path)
    if required_scope and not _principal_has_scope(principal, required_scope):
        _record_access_event(
            "access.denied", method=method, path=path,
            actor=principal.get("actor", ""), role=principal.get("role", ""),
            tenant=principal.get("tenant"), reason=f"missing_scope:{required_scope}",
            db_path=db_path,
        )
        raise HTTPException(status_code=403, detail=f"Missing scope: {required_scope}")


def _resolve_principal(request: Request, min_role: str) -> dict[str, Any]:
    """Authenticate and authorize a request, raising HTTPException on failure."""
    if not _auth_required():
        return {"role": "admin", "actor": "anonymous", "tenant": None}

    method = request.method
    path = request.url.path
    db_path = getattr(request.app.state, "db_path", "")

    principal = _authenticate(request.headers.get("x-api-key", ""), method, path, db_path)
    _authorize_role(principal, min_role, method, path, db_path)
    _authorize_scope(principal, method, path, db_path)

    # Record successful access (skip GET to reduce noise)
    if method != "GET":
        _record_access_event(
            "access.granted", method=method, path=path,
            actor=principal.get("actor", ""), role=principal.get("role", ""),
            tenant=principal.get("tenant"), db_path=db_path,
        )
    return principal


def require_viewer(request: Request) -> dict[str, Any]:
    return _resolve_principal(request, "viewer")


def require_operator(request: Request) -> dict[str, Any]:
    return _resolve_principal(request, "operator")


def require_admin(request: Request) -> dict[str, Any]:
    return _resolve_principal(request, "admin")


# ---------------------------------------------------------------------------
# Tenant enforcement helper
# ---------------------------------------------------------------------------

def enforce_tenant(
    requested_tid: str | None,
    principal: dict[str, Any],
) -> str:
    """Resolve tenant ID, raising on missing or cross-tenant violation."""
    principal_tid = principal.get("tenant")
    tid = requested_tid or principal_tid
    if not tid:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if principal_tid and tid != principal_tid and principal.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: cannot access another tenant")
    return tid


# ---------------------------------------------------------------------------
# Key rotation endpoint helper
# ---------------------------------------------------------------------------

def rotate_key(
    request: Request,
    grace_period_seconds: int = 3600,
) -> dict[str, Any]:
    """Generate a new API key, placing the old one in grace period.

    Returns the new key (plain text — this is the only time it's visible).
    The caller must add the new key to CONVERGE_API_KEYS.
    """
    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")

    hashed = hashlib.sha256(api_key.encode()).hexdigest()
    registry = _parse_api_keys()
    principal = registry.get(hashed)
    if principal is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if principal.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admin can rotate keys")

    # Generate new key
    new_key = secrets.token_urlsafe(_TOKEN_BYTES)

    # Place old key in grace period
    _register_rotated_key(hashed, dict(principal), grace_period_seconds)

    db_path = getattr(request.app.state, "db_path", "")
    _record_access_event(
        "access.key_rotated", method="POST", path="/auth/keys/rotate",
        actor=principal.get("actor", ""), role="admin",
        tenant=principal.get("tenant"),
        reason=f"grace_period={grace_period_seconds}s",
        db_path=db_path,
    )

    return {
        "new_key": new_key,
        "actor": principal.get("actor"),
        "role": principal.get("role"),
        "tenant": principal.get("tenant"),
        "scopes": principal.get("scopes"),
        "grace_period_seconds": grace_period_seconds,
        "note": "Add this key to CONVERGE_API_KEYS and remove the old one after the grace period.",
    }
