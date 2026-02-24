"""FastAPI application factory for Converge."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from converge import event_log
from converge.api.rate_limit import RateLimitMiddleware
from converge.observability import add_observability_middleware

from converge.api.routers import (
    agents,
    compliance,
    dashboard,
    events,
    health,
    intake,
    intents,
    queue,
    risk,
    security,
    webhooks,
)

log = logging.getLogger("converge.api")


def create_app(
    db_path: str | Path = "",
    webhook_secret: str = "",
) -> FastAPI:
    """Build and return a configured FastAPI application."""
    app = FastAPI(
        title="Converge",
        description="Code entropy control through semantic merge coordination",
        version="0.1.0",
    )

    # Store configuration in app state
    default_db = str(Path(".converge") / "state.db")
    resolved_db_path = str(db_path) if db_path else os.environ.get("CONVERGE_DB_PATH", default_db)
    app.state.db_path = resolved_db_path
    app.state.webhook_secret = webhook_secret or os.environ.get(
        "CONVERGE_GITHUB_WEBHOOK_SECRET", ""
    )

    # Initialise the event store from runtime env (sqlite/postgres).
    event_log.init(
        db_path=resolved_db_path,
        backend=os.environ.get("CONVERGE_DB_BACKEND"),
        dsn=os.environ.get("CONVERGE_PG_DSN"),
    )

    if not app.state.webhook_secret:
        log.warning(
            "CONVERGE_GITHUB_WEBHOOK_SECRET not set — webhook signature verification is DISABLED"
        )

    # ---------------------------------------------------------------
    # Exception handlers: match legacy {"error": "..."} format
    # ---------------------------------------------------------------

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Extract first meaningful error for concise message
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = ".".join(str(l) for l in first.get("loc", []))
            msg = first.get("msg", "Invalid input")
            detail = f"{loc}: {msg}" if loc else msg
        else:
            detail = "Invalid JSON body"
        return JSONResponse(
            status_code=400,
            content={"error": detail},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        log.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )

    # ---------------------------------------------------------------
    # Middleware (order matters — last added = outermost)
    # ---------------------------------------------------------------

    add_observability_middleware(app)

    # Rate limiting (applied after observability so throttled requests are still logged)
    if os.environ.get("CONVERGE_RATE_LIMIT_ENABLED", "1") == "1":
        app.add_middleware(RateLimitMiddleware)

    # ---------------------------------------------------------------
    # Routers — mounted at /api (legacy) and /v1 (canonical)
    # ---------------------------------------------------------------

    from fastapi import APIRouter

    api = APIRouter()
    api.include_router(intents.router)
    api.include_router(queue.router)
    api.include_router(risk.router)
    api.include_router(agents.router)
    api.include_router(compliance.router)
    api.include_router(events.router)
    api.include_router(intake.router)
    api.include_router(security.router)
    api.include_router(dashboard.router)

    app.include_router(api, prefix="/api")
    app.include_router(api, prefix="/v1")

    # Health + metrics (no auth, no version prefix)
    app.include_router(health.router)

    # Webhooks (own auth via HMAC signature)
    app.include_router(webhooks.router)

    return app
