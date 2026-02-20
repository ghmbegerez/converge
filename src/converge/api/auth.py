"""Authentication and authorization helpers for the Converge API.

Provides both standalone functions (backward compat with server_legacy)
and FastAPI dependency functions for the new ASGI server.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import HTTPException, Request


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
    "/api/audit/recent": "operator",
    "/api/compliance/thresholds/history": "operator",
}


# ---------------------------------------------------------------------------
# Core helpers (also used by server_legacy / unit tests)
# ---------------------------------------------------------------------------

def _parse_api_keys() -> dict[str, dict[str, str]]:
    """Parse CONVERGE_API_KEYS env var: key:role:actor[:tenant[:scopes]]"""
    raw = os.environ.get("CONVERGE_API_KEYS", "")
    if not raw:
        return {}
    keys: dict[str, dict[str, str]] = {}
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
                "key_prefix": k[:4],
            }
    return keys


def _auth_required() -> bool:
    return os.environ.get("CONVERGE_AUTH_REQUIRED", "1") == "1"


def _authorize_request(headers: dict[str, str], path: str) -> dict[str, Any] | None:
    """Returns principal dict or None if unauthorized.

    Kept as a standalone function for backward compatibility with unit tests
    and server_legacy.py.
    """
    if not _auth_required():
        return {"role": "admin", "actor": "anonymous", "tenant": None}

    api_key = headers.get("x-api-key", "")
    if api_key:
        hashed = hashlib.sha256(api_key.encode()).hexdigest()
        registry = _parse_api_keys()
        principal = registry.get(hashed)
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
# FastAPI dependencies
# ---------------------------------------------------------------------------

def _resolve_principal(request: Request, min_role: str) -> dict[str, Any]:
    """Authenticate and authorize a request, raising HTTPException on failure."""
    if not _auth_required():
        return {"role": "admin", "actor": "anonymous", "tenant": None}

    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")

    hashed = hashlib.sha256(api_key.encode()).hexdigest()
    registry = _parse_api_keys()
    principal = registry.get(hashed)
    if principal is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if ROLE_RANK.get(principal["role"], -1) < ROLE_RANK.get(min_role, 99):
        raise HTTPException(status_code=401, detail="Unauthorized")
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
