"""Tests for the HTTP API server: auth, validation, tenant scoping."""

import json
import os
from io import BytesIO
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from converge import event_log
from converge.api.auth import _authorize_request, _verify_github_signature


# ---------------------------------------------------------------------------
# Unit tests: auth helpers
# ---------------------------------------------------------------------------

class TestAuth:
    def test_auth_disabled_returns_admin(self, db_path):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            principal = _authorize_request({}, "/api/summary")
            assert principal is not None
            assert principal["role"] == "admin"

    def test_auth_enabled_no_key_returns_none(self, db_path):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "1", "CONVERGE_API_KEYS": ""}):
            principal = _authorize_request({}, "/api/summary")
            assert principal is None

    def test_auth_valid_key(self, db_path):
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "testkey123:admin:testactor",
        }):
            principal = _authorize_request({"x-api-key": "testkey123"}, "/api/summary")
            assert principal is not None
            assert principal["role"] == "admin"
            assert principal["actor"] == "testactor"

    def test_auth_wrong_key_returns_none(self, db_path):
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "testkey123:admin:testactor",
        }):
            principal = _authorize_request({"x-api-key": "wrongkey"}, "/api/summary")
            assert principal is None

    def test_auth_insufficient_role(self, db_path):
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "testkey123:viewer:testactor",
        }):
            # /api/audit/recent requires operator role
            principal = _authorize_request({"x-api-key": "testkey123"}, "/api/audit/recent")
            assert principal is None

    def test_auth_key_with_tenant(self, db_path):
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "testkey123:operator:testactor:team-a",
        }):
            principal = _authorize_request({"x-api-key": "testkey123"}, "/api/summary")
            assert principal["tenant"] == "team-a"


class TestGitHubSignature:
    def test_valid_signature(self, db_path):
        import hmac, hashlib
        secret = "mysecret"
        body = b'{"action": "opened"}'
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert _verify_github_signature(secret, body, sig) is True

    def test_invalid_signature(self, db_path):
        assert _verify_github_signature("secret", b"body", "sha256=wrong") is False


# ---------------------------------------------------------------------------
# Integration tests: live FastAPI/uvicorn server
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestHTTPEndpoints:
    def test_health(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            resp = urlopen(f"{live_server}/health")
            data = json.loads(resp.read())
            assert data["status"] == "ok"

    def test_get_intents_empty(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            resp = urlopen(f"{live_server}/api/intents")
            data = json.loads(resp.read())
            assert data == []

    def test_not_found(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            req = Request(f"{live_server}/api/nonexistent")
            try:
                urlopen(req)
                assert False, "Expected 404"
            except HTTPError as e:
                assert e.code == 404

    def test_post_agent_policy_missing_field(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            req = Request(
                f"{live_server}/api/agent/policy",
                data=json.dumps({}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(req)
                assert False, "Expected 400"
            except HTTPError as e:
                assert e.code == 400
                body = json.loads(e.read())
                assert "agent_id" in body["error"]

    def test_post_agent_authorize_missing_fields(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            req = Request(
                f"{live_server}/api/agent/authorize",
                data=json.dumps({"agent_id": "bot-1"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(req)
                assert False, "Expected 400"
            except HTTPError as e:
                assert e.code == 400
                body = json.loads(e.read())
                assert "action" in body["error"] or "intent_id" in body["error"]

    def test_post_invalid_json(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            req = Request(
                f"{live_server}/api/risk/policy",
                data=b"not-json{{{",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(req)
                assert False, "Expected 400"
            except HTTPError as e:
                assert e.code == 400 or e.code == 422
                body = json.loads(e.read())
                assert "error" in body

    def test_post_risk_policy_requires_tenant(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            req = Request(
                f"{live_server}/api/risk/policy",
                data=json.dumps({"max_risk_score": 50}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(req)
                assert False, "Expected 400"
            except HTTPError as e:
                assert e.code == 400

    def test_auth_required_returns_401(self, live_server):
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "1", "CONVERGE_API_KEYS": ""}):
            req = Request(f"{live_server}/api/intents")
            try:
                urlopen(req)
                assert False, "Expected 401"
            except HTTPError as e:
                assert e.code == 401


@pytest.mark.integration
class TestTenantEnforcement:
    def test_enforce_tenant_admin_can_cross(self, db_path, live_server):
        """Admin role can access any tenant."""
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "adminkey:admin:admin-user:team-a",
        }):
            req = Request(
                f"{live_server}/api/risk/policy",
                data=json.dumps({"tenant_id": "team-b", "max_risk_score": 50}).encode(),
                headers={"Content-Type": "application/json", "x-api-key": "adminkey"},
                method="POST",
            )
            resp = urlopen(req)
            data = json.loads(resp.read())
            assert data["ok"] is True

    def test_enforce_tenant_non_admin_blocked(self, db_path, live_server):
        """Non-admin cannot write to another tenant."""
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "opkey:operator:op-user:team-a",
        }):
            req = Request(
                f"{live_server}/api/risk/policy",
                data=json.dumps({"tenant_id": "team-b", "max_risk_score": 50}).encode(),
                headers={"Content-Type": "application/json", "x-api-key": "opkey"},
                method="POST",
            )
            try:
                urlopen(req)
                assert False, "Expected 403"
            except HTTPError as e:
                assert e.code == 403


# ---------------------------------------------------------------------------
# Webhook: idempotency & repo namespacing
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestWebhook:
    def _post_webhook(self, url, payload, event_type="pull_request",
                      delivery_id="d-001"):
        body = json.dumps(payload).encode()
        req = Request(
            f"{url}/integrations/github/webhook",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-github-event": event_type,
                "x-github-delivery": delivery_id,
            },
            method="POST",
        )
        resp = urlopen(req)
        return json.loads(resp.read())

    def test_webhook_creates_intent_with_repo_namespace(self, live_server, db_path):
        """Intent ID is namespaced by repo full_name."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            payload = {
                "action": "opened",
                "repository": {"full_name": "acme/backend"},
                "pull_request": {
                    "number": 42,
                    "title": "Add feature",
                    "head": {"ref": "feature/x", "sha": "abc456def789"},
                    "base": {"ref": "main", "sha": "abc123"},
                },
            }
            result = self._post_webhook(live_server, payload)
            assert result["ok"] is True

            intent = event_log.get_intent("acme/backend:pr-42")
            assert intent is not None
            assert intent.source == "feature/x"
            assert intent.technical["repo"] == "acme/backend"

    def test_webhook_different_repos_no_collision(self, live_server, db_path):
        """Two repos with same PR number get different intent IDs."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            for repo in ("acme/frontend", "acme/backend"):
                payload = {
                    "action": "opened",
                    "repository": {"full_name": repo},
                    "pull_request": {
                        "number": 1,
                        "title": "Init",
                        "head": {"ref": "feature/init", "sha": "sha001002003"},
                        "base": {"ref": "main", "sha": "000"},
                    },
                }
                self._post_webhook(live_server, payload, delivery_id=f"d-{repo}")

            assert event_log.get_intent("acme/frontend:pr-1") is not None
            assert event_log.get_intent("acme/backend:pr-1") is not None

    def test_webhook_idempotency_duplicate_delivery(self, live_server, db_path):
        """Same delivery_id is processed only once."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            payload = {
                "action": "opened",
                "repository": {"full_name": "acme/api"},
                "pull_request": {
                    "number": 99,
                    "title": "Dup test",
                    "head": {"ref": "feature/dup", "sha": "dup999888777"},
                    "base": {"ref": "main", "sha": "def456"},
                },
            }
            r1 = self._post_webhook(live_server, payload, delivery_id="dup-001")
            assert r1["ok"] is True
            assert "duplicate" not in r1

            r2 = self._post_webhook(live_server, payload, delivery_id="dup-001")
            assert r2["ok"] is True
            assert r2["duplicate"] is True

            # Only one webhook event recorded
            events = event_log.query(event_type="webhook.received")
            dup_events = [e for e in events if e["evidence"].get("delivery_id") == "dup-001"]
            assert len(dup_events) == 1

    def test_webhook_rejected_without_secret_in_production(self, db_path, live_server):
        """In production mode (auth required), webhooks are rejected when no secret is configured."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "1"}):
            payload = {"action": "opened", "repository": {"full_name": "acme/x"},
                       "pull_request": {"number": 1, "title": "t",
                                        "head": {"ref": "f", "sha": "f00"}, "base": {"ref": "main", "sha": "0"}}}
            body = json.dumps(payload).encode()
            req = Request(
                f"{live_server}/integrations/github/webhook",
                data=body,
                headers={"Content-Type": "application/json",
                         "x-github-event": "pull_request",
                         "x-github-delivery": "reject-001"},
                method="POST",
            )
            try:
                urlopen(req)
                assert False, "Expected 403"
            except HTTPError as e:
                assert e.code == 403
                body = json.loads(e.read())
                assert "not configured" in body["error"]


@pytest.mark.integration
class TestTenantIsolationReads:
    """GET endpoints filter by tenant for non-admin principals."""

    def test_risk_policy_filtered_by_tenant(self, live_server, db_path):
        """Non-admin user only sees their own tenant's risk policies."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            # Seed risk policies for two tenants
            event_log.upsert_risk_policy("team-a", {"max_risk_score": 50})
            event_log.upsert_risk_policy("team-b", {"max_risk_score": 70})

        # Non-admin user from team-a
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "viewkey:viewer:viewer-user:team-a",
        }):
            req = Request(f"{live_server}/api/risk/policy",
                          headers={"x-api-key": "viewkey"})
            resp = urlopen(req)
            data = json.loads(resp.read())
            tenant_ids = [p["tenant_id"] for p in data]
            assert "team-a" in tenant_ids
            assert "team-b" not in tenant_ids

    def test_risk_policy_admin_sees_all(self, live_server, db_path):
        """Admin user sees all tenants' risk policies."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            event_log.upsert_risk_policy("team-x", {"max_risk_score": 40})
            event_log.upsert_risk_policy("team-y", {"max_risk_score": 60})

        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "adminkey:admin:admin-user",
        }):
            req = Request(f"{live_server}/api/risk/policy",
                          headers={"x-api-key": "adminkey"})
            resp = urlopen(req)
            data = json.loads(resp.read())
            # Admin has no tenant → sees all
            assert len(data) >= 2
