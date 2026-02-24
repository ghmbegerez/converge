"""Tests for health checks, metrics, and v1 prefix."""

import json
import os
import socket
import threading
import time
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from converge import event_log
from converge.models import Event


@pytest.fixture
def live_server(db_path):
    """Start a FastAPI/uvicorn server on a random port for testing."""
    import uvicorn
    from converge.api import create_app

    app = create_app(db_path=str(db_path), webhook_secret="")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.integration
class TestHealthChecks:
    def test_health_legacy(self, db_path, live_server):
        resp = urlopen(f"{live_server}/health")
        data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert "timestamp" in data

    def test_health_ready(self, db_path, live_server):
        resp = urlopen(f"{live_server}/health/ready")
        data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_health_live(self, db_path, live_server):
        resp = urlopen(f"{live_server}/health/live")
        data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_metrics_endpoint(self, db_path, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            # Make a request first so there's something to measure
            urlopen(f"{live_server}/health")
            resp = urlopen(f"{live_server}/metrics")
            body = resp.read().decode()
            assert "converge_http_requests_total" in body

    def test_health_no_auth_required(self, db_path, live_server):
        """Health endpoints should work even with auth enabled."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "1", "CONVERGE_API_KEYS": ""}):
            resp = urlopen(f"{live_server}/health")
            data = json.loads(resp.read())
            assert data["status"] == "ok"

            resp = urlopen(f"{live_server}/health/ready")
            assert json.loads(resp.read())["status"] == "ok"

            resp = urlopen(f"{live_server}/health/live")
            assert json.loads(resp.read())["status"] == "ok"


@pytest.mark.integration
class TestV1Prefix:
    """Verify that /v1/ prefix serves the same endpoints as /api/."""

    def test_v1_intents(self, db_path, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            resp = urlopen(f"{live_server}/v1/intents")
            data = json.loads(resp.read())
            assert data == []

    def test_v1_summary(self, db_path, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            resp = urlopen(f"{live_server}/v1/summary")
            data = json.loads(resp.read())
            assert "health" in data
            assert "queue" in data

    def test_v1_queue_state(self, db_path, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            resp = urlopen(f"{live_server}/v1/queue/state")
            data = json.loads(resp.read())
            assert "total" in data

    def test_v1_risk_policy(self, live_server, db_path):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            event_log.upsert_risk_policy("t1", {"score": 10})
            resp = urlopen(f"{live_server}/v1/risk/policy")
            data = json.loads(resp.read())
            assert len(data) >= 1

    def test_v1_events(self, live_server, db_path):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            event_log.append(Event(
                event_type="test.event", payload={"k": "v"}, trace_id="t",
            ))
            resp = urlopen(f"{live_server}/v1/events")
            data = json.loads(resp.read())
            assert len(data) >= 1

    def test_v1_compliance_report(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            resp = urlopen(f"{live_server}/v1/compliance/report")
            data = json.loads(resp.read())
            assert "passed" in data

    def test_v1_agent_policy(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            resp = urlopen(f"{live_server}/v1/agent/policy")
            data = json.loads(resp.read())
            assert isinstance(data, list)

    def test_v1_auth_whoami(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            resp = urlopen(f"{live_server}/v1/auth/whoami")
            data = json.loads(resp.read())
            assert data["role"] == "admin"


@pytest.mark.integration
class TestContractParity:
    """Verify /api and /v1 return identical results."""

    def test_intents_parity(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            api_resp = json.loads(urlopen(f"{live_server}/api/intents").read())
            v1_resp = json.loads(urlopen(f"{live_server}/v1/intents").read())
            assert api_resp == v1_resp

    def test_queue_state_parity(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            api_resp = json.loads(urlopen(f"{live_server}/api/queue/state").read())
            v1_resp = json.loads(urlopen(f"{live_server}/v1/queue/state").read())
            assert api_resp == v1_resp
