"""Provider-side shape tests — verify converge API response shapes.

These tests verify that converge emits the fields that orchestrator and
converge-ui consume.  When a field is renamed or removed in converge,
the corresponding consumer test should fail.

All tests run against a TestClient (in-process, no network).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from converge import event_log
from converge.api import create_app
from converge.models import Event, EventType, Intent, RiskLevel, Status


@pytest.fixture
def client(db_path):
    with patch.dict(os.environ, {
        "CONVERGE_AUTH_REQUIRED": "0",
        "CONVERGE_RATE_LIMIT_ENABLED": "0",
    }):
        app = create_app(db_path=str(db_path))
        yield TestClient(app)


@pytest.fixture
def seeded_intent(db_path) -> Intent:
    """Persist a sample intent so GET endpoints return data."""
    intent = Intent(
        id="shape-001",
        source="feature/shape-test",
        target="main",
        status=Status.READY,
        risk_level=RiskLevel.MEDIUM,
        priority=2,
        tenant_id="team-a",
    )
    event_log.upsert_intent(intent)
    # Add an event so /events returns something
    event_log.append(Event(
        event_type=EventType.INTENT_CREATED,
        intent_id=intent.id,
        tenant_id=intent.tenant_id,
        payload=intent.to_dict(),
    ))
    return intent


# ---------------------------------------------------------------------------
# Intent shapes (consumed by orchestrator + UI)
# ---------------------------------------------------------------------------


class TestIntentShape:
    """GET /v1/intents/{id} must include fields orchestrator and UI parse."""

    def test_intent_has_required_fields(self, client, seeded_intent):
        resp = client.get(f"/v1/intents/{seeded_intent.id}")
        assert resp.status_code == 200
        data = resp.json()
        for field in ("id", "status", "source", "target"):
            assert field in data, f"intent must have '{field}'"

    def test_intent_has_metadata_fields(self, client, seeded_intent):
        resp = client.get(f"/v1/intents/{seeded_intent.id}")
        data = resp.json()
        for field in ("risk_level", "priority", "created_at"):
            assert field in data, f"intent must have '{field}'"


class TestIntentListShape:
    """GET /v1/intents must return a list where each item has id + status."""

    def test_list_returns_array(self, client, seeded_intent):
        resp = client.get("/v1/intents")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list), "intents list must be an array"

    def test_list_items_have_id_and_status(self, client, seeded_intent):
        resp = client.get("/v1/intents")
        data = resp.json()
        assert len(data) >= 1
        for item in data:
            assert "id" in item, "each intent must have 'id'"
            assert "status" in item, "each intent must have 'status'"


# ---------------------------------------------------------------------------
# Reviews shapes (consumed by UI)
# ---------------------------------------------------------------------------


class TestReviewsListShape:
    """GET /v1/reviews must return {reviews: [...], total: int}."""

    def test_reviews_top_level_keys(self, client, db_path):
        resp = client.get("/v1/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert "reviews" in data, "reviews response must have 'reviews' key"
        assert "total" in data, "reviews response must have 'total' key"
        assert isinstance(data["reviews"], list)

    def test_review_item_has_status(self, client, seeded_intent):
        # Create a review first
        client.post("/v1/reviews", json={"intent_id": seeded_intent.id})
        resp = client.get("/v1/reviews")
        data = resp.json()
        if data["reviews"]:
            assert "status" in data["reviews"][0], "review item must have 'status'"


class TestReviewsSummaryShape:
    """GET /v1/reviews/summary must return dict with counters."""

    def test_summary_has_counters(self, client, db_path):
        resp = client.get("/v1/reviews/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict), "summary must be a dict"
        assert "total" in data, "summary must have 'total'"
        assert "by_status" in data, "summary must have 'by_status'"


# ---------------------------------------------------------------------------
# Compliance shapes (consumed by UI)
# ---------------------------------------------------------------------------


class TestComplianceReportShape:
    """GET /v1/compliance/report must include passed boolean and alerts."""

    def test_report_has_passed(self, client, db_path):
        resp = client.get("/v1/compliance/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "passed" in data, "compliance report must have 'passed'"
        assert isinstance(data["passed"], bool), "'passed' must be boolean"

    def test_report_has_alerts(self, client, db_path):
        resp = client.get("/v1/compliance/report")
        data = resp.json()
        assert "alerts" in data, "compliance report must have 'alerts'"
        assert isinstance(data["alerts"], list), "'alerts' must be a list"


class TestComplianceAlertsShape:
    """GET /v1/compliance/alerts must return a list."""

    def test_alerts_is_list(self, client, db_path):
        resp = client.get("/v1/compliance/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list), "compliance alerts must be a list"


# ---------------------------------------------------------------------------
# Risk shapes (consumed by UI)
# ---------------------------------------------------------------------------


class TestRiskReviewShape:
    """GET /v1/risk/review?intent_id=... must return a dict with risk info."""

    def test_risk_review_returns_dict(self, client, seeded_intent):
        resp = client.get("/v1/risk/review", params={"intent_id": seeded_intent.id})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict), "risk review must be a dict"


class TestRiskGateReportShape:
    """GET /v1/risk/gate/report must return evaluation stats."""

    def test_gate_report_has_fields(self, client, db_path):
        resp = client.get("/v1/risk/gate/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_evaluations" in data, "gate report must have 'total_evaluations'"
        assert "block_rate" in data, "gate report must have 'block_rate'"


# ---------------------------------------------------------------------------
# Dashboard shapes (consumed by UI)
# ---------------------------------------------------------------------------


class TestDashboardShape:
    """GET /v1/dashboard must return dict with health, queue, compliance."""

    def test_dashboard_has_sections(self, client, db_path):
        resp = client.get("/v1/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict), "dashboard must be a dict"
        for section in ("health", "queue", "compliance"):
            assert section in data, f"dashboard must have '{section}'"


class TestDashboardAlertsShape:
    """GET /v1/dashboard/alerts must return {alerts: list}."""

    def test_alerts_key_exists(self, client, db_path):
        resp = client.get("/v1/dashboard/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert "alerts" in data, "dashboard alerts must have 'alerts' key"
        assert isinstance(data["alerts"], list)


# ---------------------------------------------------------------------------
# Events shape (consumed by orchestrator + UI)
# ---------------------------------------------------------------------------


class TestIntentEventsShape:
    """GET /v1/intents/{id}/events must return a list of event dicts."""

    def test_events_returns_list(self, client, seeded_intent):
        resp = client.get(f"/v1/intents/{seeded_intent.id}/events")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list), "events must be a list"

    def test_event_items_have_type(self, client, seeded_intent):
        resp = client.get(f"/v1/intents/{seeded_intent.id}/events")
        data = resp.json()
        assert len(data) >= 1, "seeded intent should have at least one event"
        assert "event_type" in data[0], "event must have 'event_type'"


# ---------------------------------------------------------------------------
# Create intent shape (consumed by orchestrator)
# ---------------------------------------------------------------------------


class TestCreateIntentShape:
    """POST /v1/intents must return {ok, intent_id}."""

    def test_create_returns_ok_and_id(self, client, db_path):
        resp = client.post("/v1/intents", json={
            "source": "feature/contract-test",
            "target": "main",
            "status": "READY",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data, "create intent must have 'ok'"
        assert "intent_id" in data, "create intent must have 'intent_id'"
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# Summary shape (consumed by UI)
# ---------------------------------------------------------------------------


class TestSummaryShape:
    """GET /v1/summary must return {health, queue}."""

    def test_summary_has_health_and_queue(self, client, db_path):
        resp = client.get("/v1/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "health" in data, "summary must have 'health'"
        assert "queue" in data, "summary must have 'queue'"
