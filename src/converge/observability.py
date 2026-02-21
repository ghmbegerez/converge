"""Observability: structured logging, OTLP tracing, Prometheus metrics.

OTLP tracing is optional — works when opentelemetry packages are installed,
degrades gracefully otherwise.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, Request, Response

# --- Metrics constants ---
_HTTP_ERROR_THRESHOLD = 500
_MS_PER_SECOND = 1000


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_dict: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_dict["exception"] = self.formatException(record.exc_info)
        # Propagate extra fields (trace_id, etc.)
        for key in ("trace_id", "intent_id", "tenant_id", "method", "path", "status_code", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                log_dict[key] = val
        return json.dumps(log_dict, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON output."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Quiet noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# OTLP tracing (optional)
# ---------------------------------------------------------------------------

_tracer = None

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    _HAS_OTLP = True
except ImportError:
    _HAS_OTLP = False


def setup_tracing(service_name: str = "converge") -> None:
    """Initialise OpenTelemetry tracing if packages are available."""
    global _tracer
    if not _HAS_OTLP:
        return
    provider = TracerProvider()
    # Try OTLP exporter first, fall back to console
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HTTPExporter
        provider.add_span_processor(BatchSpanProcessor(HTTPExporter()))
    except ImportError:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)


def get_tracer():
    """Return the OTLP tracer (or None if not available)."""
    return _tracer


# ---------------------------------------------------------------------------
# Prometheus-compatible metrics (no external dependency)
# ---------------------------------------------------------------------------

_request_count: dict[tuple[str, str, str], int] = defaultdict(int)
_request_latency_sum: dict[tuple[str, str], float] = defaultdict(float)
_request_latency_count: dict[tuple[str, str], int] = defaultdict(int)
_error_count: dict[tuple[str, str], int] = defaultdict(int)


def record_request(method: str, path: str, status: int, duration: float) -> None:
    _request_count[(method, path, str(status))] += 1
    _request_latency_sum[(method, path)] += duration
    _request_latency_count[(method, path)] += 1
    if status >= _HTTP_ERROR_THRESHOLD:
        _error_count[(method, path)] += 1


def generate_metrics() -> str:
    """Render metrics in Prometheus text exposition format."""
    lines: list[str] = []

    lines.append("# HELP converge_http_requests_total Total HTTP requests by method, path, status.")
    lines.append("# TYPE converge_http_requests_total counter")
    for (method, path, status), count in sorted(_request_count.items()):
        lines.append(f'converge_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}')

    lines.append("# HELP converge_http_request_duration_seconds Total request duration by method and path.")
    lines.append("# TYPE converge_http_request_duration_seconds summary")
    for (method, path), total in sorted(_request_latency_sum.items()):
        cnt = _request_latency_count[(method, path)]
        lines.append(f'converge_http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {total:.6f}')
        lines.append(f'converge_http_request_duration_seconds_count{{method="{method}",path="{path}"}} {cnt}')

    lines.append("# HELP converge_http_errors_total Total 5xx errors.")
    lines.append("# TYPE converge_http_errors_total counter")
    for (method, path), count in sorted(_error_count.items()):
        lines.append(f'converge_http_errors_total{{method="{method}",path="{path}"}} {count}')

    # Rate limiting metrics
    try:
        from converge.api.rate_limit import get_limiter
        limiter = get_limiter()
        lines.append("# HELP converge_rate_limit_throttled_total Total throttled requests by tenant.")
        lines.append("# TYPE converge_rate_limit_throttled_total counter")
        for tenant, cnt in sorted(limiter.throttled_by_tenant.items()):
            lines.append(f'converge_rate_limit_throttled_total{{tenant="{tenant}"}} {cnt}')
        lines.append(f"converge_rate_limit_throttled_global {limiter.total_throttled}")
    except Exception:
        pass  # rate limiter not initialized

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# FastAPI middleware
# ---------------------------------------------------------------------------

def add_observability_middleware(app: FastAPI) -> None:
    """Add request logging and metrics collection middleware."""

    @app.middleware("http")
    async def observe_request(request: Request, call_next) -> Response:
        start = time.time()
        response: Response = await call_next(request)
        duration = time.time() - start

        # Normalise path for metrics (strip query string, collapse IDs)
        path = request.url.path
        method = request.method
        status = response.status_code

        record_request(method, path, status, duration)

        logger = logging.getLogger("converge.access")
        logger.info(
            "%s %s %d %.0fms",
            method, path, status, duration * _MS_PER_SECOND,
            extra={
                "method": method,
                "path": path,
                "status_code": status,
                "duration_ms": round(duration * _MS_PER_SECOND, 1),
                "trace_id": request.headers.get("x-trace-id", ""),
            },
        )
        return response
