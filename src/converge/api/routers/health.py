"""Health check and metrics endpoints (no auth required)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from converge import event_log
from converge.models import now_iso
from converge.observability import generate_metrics

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok", "timestamp": now_iso()}


@router.get("/health/ready")
def health_ready(request: Request):
    """Readiness probe — verifies the database is accessible."""
    db_path = request.app.state.db_path
    try:
        event_log.count(db_path)
        return {"status": "ok", "timestamp": now_iso()}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "error": str(e), "timestamp": now_iso()},
        )


@router.get("/health/live")
def health_live():
    """Liveness probe — process is alive."""
    return {"status": "ok"}


@router.get("/metrics")
def metrics():
    """Prometheus-compatible metrics endpoint."""
    return Response(content=generate_metrics(), media_type="text/plain; charset=utf-8")
