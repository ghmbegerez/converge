"""Tests for API app factory initialization behavior."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from converge import event_log
from converge.api import create_app


def test_create_app_without_db_path_uses_env_db_for_readiness(db_path, tmp_path):
    """create_app() should initialize storage from env even without db_path."""
    db_file = tmp_path / "state.db"
    with patch.dict(
        "os.environ",
        {
            "CONVERGE_DB_BACKEND": "sqlite",
            "CONVERGE_DB_PATH": str(db_file),
            "CONVERGE_AUTH_REQUIRED": "0",
        },
        clear=False,
    ):
        app = create_app(webhook_secret="")
        client = TestClient(app)
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    event_log.close()
