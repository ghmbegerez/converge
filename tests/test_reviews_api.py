"""Tests for review lifecycle HTTP endpoints."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from converge import event_log
from converge.api import create_app
from converge.models import Intent, RiskLevel, Status


@pytest.fixture
def client(db_path):
    with patch.dict(os.environ, {
        "CONVERGE_AUTH_REQUIRED": "0",
        "CONVERGE_RATE_LIMIT_ENABLED": "0",
    }):
        app = create_app(db_path=str(db_path))
        yield TestClient(app)


@pytest.fixture
def seed_intent(db_path):
    """Create a sample intent for review tests."""
    intent = Intent(
        id="intent-rev-001",
        source="feature/review-test",
        target="main",
        status=Status.READY,
        risk_level=RiskLevel.MEDIUM,
        priority=2,
        tenant_id="team-a",
    )
    event_log.upsert_intent(intent)
    return intent


class TestRequestReview:
    def test_request_review(self, client, seed_intent):
        resp = client.post("/api/reviews", json={"intent_id": "intent-rev-001"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent_id"] == "intent-rev-001"
        assert data["status"] == "pending"

    def test_request_review_with_reviewer(self, client, seed_intent):
        resp = client.post("/api/reviews", json={
            "intent_id": "intent-rev-001",
            "reviewer": "alice",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "assigned"
        assert data["reviewer"] == "alice"

    def test_request_review_not_found(self, client, db_path):
        resp = client.post("/api/reviews", json={"intent_id": "nonexistent"})
        assert resp.status_code == 404


class TestAssignReview:
    def test_assign_review(self, client, seed_intent):
        # Create a review first
        create_resp = client.post("/api/reviews", json={"intent_id": "intent-rev-001"})
        task_id = create_resp.json()["id"]

        resp = client.post(f"/api/reviews/{task_id}/assign", json={"reviewer": "bob"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["reviewer"] == "bob"
        assert data["status"] == "assigned"

    def test_assign_review_not_found(self, client, db_path):
        resp = client.post("/api/reviews/nonexistent/assign", json={"reviewer": "bob"})
        assert resp.status_code == 404


class TestCompleteReview:
    def test_complete_review(self, client, seed_intent):
        create_resp = client.post("/api/reviews", json={
            "intent_id": "intent-rev-001",
            "reviewer": "alice",
        })
        task_id = create_resp.json()["id"]

        resp = client.post(f"/api/reviews/{task_id}/complete", json={
            "resolution": "approved",
            "notes": "Looks good",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"

    def test_complete_review_not_found(self, client, db_path):
        resp = client.post("/api/reviews/nonexistent/complete", json={})
        assert resp.status_code == 404


class TestCancelReview:
    def test_cancel_review(self, client, seed_intent):
        create_resp = client.post("/api/reviews", json={"intent_id": "intent-rev-001"})
        task_id = create_resp.json()["id"]

        resp = client.post(f"/api/reviews/{task_id}/cancel", json={"reason": "No longer needed"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"

    def test_cancel_review_not_found(self, client, db_path):
        resp = client.post("/api/reviews/nonexistent/cancel", json={})
        assert resp.status_code == 404


class TestEscalateReview:
    def test_escalate_review(self, client, seed_intent):
        create_resp = client.post("/api/reviews", json={
            "intent_id": "intent-rev-001",
            "reviewer": "alice",
        })
        task_id = create_resp.json()["id"]

        resp = client.post(f"/api/reviews/{task_id}/escalate", json={"reason": "sla_breach"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "escalated"

    def test_escalate_review_not_found(self, client, db_path):
        resp = client.post("/api/reviews/nonexistent/escalate", json={})
        assert resp.status_code == 404
