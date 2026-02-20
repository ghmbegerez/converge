"""S6 tests: dashboard endpoint, export HTTP, deployment validation."""

from __future__ import annotations

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
from converge.models import Event, EventType, Intent, RiskLevel, Status, now_iso


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def live_server(db_path):
    import uvicorn
    from converge.api import create_app

    with patch.dict(os.environ, {
        "CONVERGE_AUTH_REQUIRED": "0",
        "CONVERGE_RATE_LIMIT_ENABLED": "0",
    }):
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


def _seed_data(db_path):
    """Seed the database with sample intents and events for dashboard tests."""
    intent = Intent(
        id="dash-test-001",
        source="feature/dashboard",
        target="main",
        status=Status.MERGED,
        created_by="test",
        risk_level=RiskLevel.MEDIUM,
        priority=2,
        tenant_id="team-a",
    )
    event_log.upsert_intent(db_path, intent)

    # Simulation event
    event_log.append(db_path, Event(
        event_type=EventType.SIMULATION_COMPLETED,
        intent_id="dash-test-001",
        tenant_id="team-a",
        payload={
            "mergeable": True,
            "conflicts": [],
            "files_changed": ["src/app.py", "src/utils.py"],
            "source": "feature/dashboard",
            "target": "main",
        },
    ))

    # Risk event
    event_log.append(db_path, Event(
        event_type=EventType.RISK_EVALUATED,
        intent_id="dash-test-001",
        tenant_id="team-a",
        payload={
            "intent_id": "dash-test-001",
            "risk_score": 25.5,
            "damage_score": 12.0,
            "entropy_score": 8.0,
            "propagation_score": 5.0,
            "containment_score": 0.85,
            "signals": {
                "entropic_load": 3.2,
                "contextual_value": 1.5,
                "complexity_delta": 2.1,
                "path_dependence": 0.3,
            },
            "findings": [],
            "impact_edges": [],
            "graph_metrics": {"nodes": 10, "edges": 15, "density": 0.33},
            "bombs": [],
        },
    ))

    # Policy event
    event_log.append(db_path, Event(
        event_type=EventType.POLICY_EVALUATED,
        intent_id="dash-test-001",
        tenant_id="team-a",
        payload={
            "verdict": "ALLOW",
            "gates": [
                {"gate": "verification", "passed": True},
                {"gate": "containment", "passed": True},
                {"gate": "entropy", "passed": True},
            ],
            "profile_used": "medium",
        },
    ))

    # A second READY intent
    intent2 = Intent(
        id="dash-test-002",
        source="feature/login",
        target="main",
        status=Status.READY,
        created_by="test",
        risk_level=RiskLevel.HIGH,
        priority=1,
        tenant_id="team-a",
    )
    event_log.upsert_intent(db_path, intent2)


# ---------------------------------------------------------------------------
# Dashboard endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDashboard:
    def test_dashboard_returns_all_sections(self, live_server, db_path):
        """Dashboard endpoint returns health, queue, compliance, risk_trend, predictions, metrics."""
        _seed_data(db_path)

        resp = urlopen(f"{live_server}/api/dashboard")
        data = json.loads(resp.read())

        assert "health" in data
        assert "queue" in data
        assert "compliance" in data
        assert "risk_trend" in data
        assert "predictions" in data
        assert "metrics" in data

        # Health section has expected fields
        assert "repo_health_score" in data["health"]
        assert "status" in data["health"]

        # Queue section
        assert "total" in data["queue"]
        assert "by_status" in data["queue"]

        # Compliance section
        assert "passed" in data["compliance"]
        assert "alerts" in data["compliance"]

    def test_dashboard_with_tenant_filter(self, live_server, db_path):
        """Dashboard filters by tenant when specified."""
        _seed_data(db_path)

        resp = urlopen(f"{live_server}/api/dashboard?tenant_id=team-a")
        data = json.loads(resp.read())
        assert data["health"] is not None

    def test_dashboard_empty_db(self, live_server, db_path):
        """Dashboard works on empty database."""
        resp = urlopen(f"{live_server}/api/dashboard")
        data = json.loads(resp.read())
        assert data["health"]["repo_health_score"] >= 0
        assert data["queue"]["total"] == 0

    def test_dashboard_alerts_endpoint(self, live_server, db_path):
        """Dashboard alerts combines compliance and predictions."""
        _seed_data(db_path)

        resp = urlopen(f"{live_server}/api/dashboard/alerts")
        data = json.loads(resp.read())

        assert "compliance_passed" in data
        assert "alerts" in data
        assert "total" in data
        assert isinstance(data["alerts"], list)

    def test_dashboard_alerts_empty(self, live_server, db_path):
        """Dashboard alerts on empty db returns empty list."""
        resp = urlopen(f"{live_server}/api/dashboard/alerts")
        data = json.loads(resp.read())
        assert data["total"] >= 0


# ---------------------------------------------------------------------------
# Export endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestExportHTTP:
    def test_export_jsonl(self, live_server, db_path):
        """Export decisions as JSONL via HTTP."""
        _seed_data(db_path)

        resp = urlopen(f"{live_server}/api/export/decisions?fmt=jsonl")
        body = resp.read().decode()

        assert resp.headers.get("Content-Type").startswith("application/x-ndjson")

        lines = [l for l in body.strip().split("\n") if l]
        assert len(lines) >= 1

        # Each line is valid JSON
        for line in lines:
            record = json.loads(line)
            assert "intent_id" in record
            assert "status" in record

    def test_export_csv(self, live_server, db_path):
        """Export decisions as CSV via HTTP."""
        _seed_data(db_path)

        resp = urlopen(f"{live_server}/api/export/decisions?fmt=csv")
        body = resp.read().decode()

        assert resp.headers.get("Content-Type").startswith("text/csv")

        lines = body.strip().split("\n")
        assert len(lines) >= 2  # header + at least 1 row
        assert "intent_id" in lines[0]  # header contains expected field

    def test_export_empty_db_jsonl(self, live_server, db_path):
        """Export from empty database returns empty JSONL."""
        resp = urlopen(f"{live_server}/api/export/decisions?fmt=jsonl")
        body = resp.read().decode()
        assert body == "" or body.strip() == ""

    def test_export_empty_db_csv(self, live_server, db_path):
        """Export from empty database returns empty CSV."""
        resp = urlopen(f"{live_server}/api/export/decisions?fmt=csv")
        body = resp.read().decode()
        assert body == "" or body.strip() == ""


# ---------------------------------------------------------------------------
# Deployment validation tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDeploymentSmoke:
    def test_health_live(self, live_server):
        """Liveness probe works."""
        resp = urlopen(f"{live_server}/health/live")
        data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_health_ready(self, live_server):
        """Readiness probe works with database."""
        resp = urlopen(f"{live_server}/health/ready")
        data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_metrics_endpoint(self, live_server):
        """Prometheus metrics endpoint returns text."""
        resp = urlopen(f"{live_server}/metrics")
        body = resp.read().decode()
        assert "converge_" in body or "http_" in body or len(body) > 0

    def test_v1_prefix_works(self, live_server, db_path):
        """V1 prefix routes to same endpoints as /api."""
        _seed_data(db_path)

        resp = urlopen(f"{live_server}/v1/dashboard")
        data = json.loads(resp.read())
        assert "health" in data
