"""HTTP API server — FastAPI / uvicorn based."""

from __future__ import annotations

import json
import os
from pathlib import Path

from converge.api import create_app
from converge.api.auth import (  # noqa: F401 — backward compat re-exports
    API_ROLE_MAP,
    ROLE_RANK,
    _authorize_request,
)
from converge.models import now_iso


def serve(
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 9876,
    webhook_secret: str = "",
) -> None:
    """Start the ASGI server (uvicorn)."""
    import uvicorn

    from converge.observability import setup_logging, setup_tracing

    setup_logging()
    setup_tracing()

    app = create_app(db_path=db_path, webhook_secret=webhook_secret)

    print(json.dumps({"event": "server_started", "host": host, "port": port, "timestamp": now_iso()}))
    uvicorn.run(app, host=host, port=port, log_level="info")
