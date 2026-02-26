"""HTTP API server — FastAPI / uvicorn based."""

from __future__ import annotations

import json
import os

from converge.api import create_app
from converge.api.auth import (  # noqa: F401 — backward compat re-exports
    API_ROLE_MAP,
    ROLE_RANK,
    _authorize_request,
)
from converge.models import now_iso


def serve(
    host: str = "127.0.0.1",
    port: int = 9876,
    webhook_secret: str = "",
    ui_dist: str = "",
) -> None:
    """Start the ASGI server (uvicorn).

    If *ui_dist* points to a directory containing a built SPA (e.g. converge-ui/dist),
    the app will serve it at ``/`` with SPA fallback, enabling single-process deployment.
    """
    import uvicorn

    from converge.observability import setup_logging, setup_tracing

    setup_logging()
    setup_tracing()

    resolved_ui = ui_dist or os.environ.get("CONVERGE_UI_DIST", "") or None
    app = create_app(webhook_secret=webhook_secret, ui_dist=resolved_ui)

    print(json.dumps({"event": "server_started", "host": host, "port": port, "ui_dist": resolved_ui, "timestamp": now_iso()}))
    uvicorn.run(app, host=host, port=port, log_level="info")
