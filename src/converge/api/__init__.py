"""FastAPI application factory for Converge."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from converge import event_log
from converge.api.rate_limit import RateLimitMiddleware
from converge.api.routers import (
    agents,
    compliance,
    dashboard,
    demo,
    events,
    health,
    intake,
    intents,
    queue,
    reviews,
    risk,
    security,
    webhooks,
)
from converge.observability import add_observability_middleware

log = logging.getLogger("converge.api")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


def create_app(
    db_path: str | Path = "",
    webhook_secret: str = "",
    ui_dist: str | Path | None = None,
) -> FastAPI:
    """Build and return a configured FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("Converge starting up")
        yield
        log.info("Converge shutting down — releasing resources")
        store = getattr(event_log, "_store", None)
        if store is not None and hasattr(store, "close"):
            try:
                store.close()
            except Exception:
                log.warning("Error closing store during shutdown", exc_info=True)

    app = FastAPI(
        title="Converge",
        description="Code entropy control through semantic merge coordination",
        version="0.1.0",
        lifespan=lifespan,
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
        detail = exc.detail
        # If detail is already a structured dict (e.g. intake rejection), use it
        if isinstance(detail, dict):
            body = {"error": detail}
        else:
            body = {"error": {"code": f"http_{exc.status_code}", "message": str(detail)}}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
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
            content={"error": {"code": "validation_error", "message": detail}},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        log.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "An internal error occurred."}},
        )

    # ---------------------------------------------------------------
    # Middleware (order matters — last added = outermost)
    # ---------------------------------------------------------------

    add_observability_middleware(app)

    # Rate limiting (applied after observability so throttled requests are still logged)
    if os.environ.get("CONVERGE_RATE_LIMIT_ENABLED", "1") == "1":
        app.add_middleware(RateLimitMiddleware)

    # CORS — configurable origins via CONVERGE_CORS_ORIGINS env var
    cors_raw = os.environ.get(
        "CONVERGE_CORS_ORIGINS", "http://localhost:5173,http://localhost:9988"
    )
    cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]
    allow_creds = "*" not in cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_creds,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware)

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
    api.include_router(reviews.router)
    api.include_router(demo.router)
    api.include_router(dashboard.router)

    app.include_router(api, prefix="/api")
    app.include_router(api, prefix="/v1")

    # Health + metrics (no auth, no version prefix)
    app.include_router(health.router)

    # Webhooks (own auth via HMAC signature)
    app.include_router(webhooks.router)

    # ---------------------------------------------------------------
    # Optional: serve UI dist (single-binary deployment)
    # ---------------------------------------------------------------
    ui_path = Path(ui_dist) if ui_dist else None
    if ui_path and ui_path.is_dir():
        index_html = ui_path / "index.html"

        # SPA catch-all: any path not matched by API routes serves index.html
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            # Serve actual file if it exists (JS, CSS, images, etc.)
            candidate = ui_path / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            # Otherwise serve index.html for SPA routing
            if index_html.is_file():
                return FileResponse(index_html)
            raise HTTPException(404, "UI not found")

        log.info("Serving UI from %s", ui_path)

    return app
