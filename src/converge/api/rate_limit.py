"""In-process sliding-window rate limiter per tenant.

Single-instance only.  For multi-instance deployments, use a shared store
like Redis (out of scope for this stage).
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class TenantRateLimiter:
    """Sliding-window counter rate limiter keyed by tenant."""

    def __init__(self, rpm: int = 120, window_seconds: int = 60) -> None:
        self._rpm = rpm
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        # metrics
        self.total_throttled: int = 0
        self.throttled_by_tenant: dict[str, int] = defaultdict(int)

    @property
    def rpm(self) -> int:
        return self._rpm

    def is_allowed(self, tenant_id: str) -> bool:
        """Return True if the request is within rate limits."""
        now = time.monotonic()
        cutoff = now - self._window
        # Prune old entries
        entries = self._requests[tenant_id]
        self._requests[tenant_id] = [t for t in entries if t > cutoff]
        if len(self._requests[tenant_id]) >= self._rpm:
            self.total_throttled += 1
            self.throttled_by_tenant[tenant_id] += 1
            return False
        self._requests[tenant_id].append(now)
        return True

    def reset(self) -> None:
        """Clear all state (useful for tests)."""
        self._requests.clear()
        self.total_throttled = 0
        self.throttled_by_tenant.clear()


# Global limiter instance
_limiter: TenantRateLimiter | None = None


def get_limiter() -> TenantRateLimiter:
    """Return (and lazily create) the global rate limiter."""
    global _limiter
    if _limiter is None:
        rpm = int(os.environ.get("CONVERGE_RATE_LIMIT_RPM", "120"))
        _limiter = TenantRateLimiter(rpm=rpm)
    return _limiter


def reset_limiter() -> None:
    """Reset the global limiter (for tests)."""
    global _limiter
    _limiter = None


# ---------------------------------------------------------------------------
# Paths exempt from rate limiting
# ---------------------------------------------------------------------------

_EXEMPT_PREFIXES = ("/health", "/metrics")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces per-tenant rate limits."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        # Health/metrics always allowed
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        limiter = get_limiter()
        # Determine tenant from header or default
        tenant = request.headers.get("x-tenant-id", "_anonymous")
        # Also check x-api-key to resolve tenant (lightweight, no full auth)
        if tenant == "_anonymous":
            tenant = request.headers.get("x-api-key", "_anonymous")[:8] if request.headers.get("x-api-key") else "_anonymous"

        if not limiter.is_allowed(tenant):
            return JSONResponse(
                status_code=429,
                content={"error": f"Rate limit exceeded ({limiter.rpm} requests/min)"},
            )
        return await call_next(request)
