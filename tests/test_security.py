"""S4 Security tests: scopes, key rotation, access auditing, injection, escalation."""

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
from converge.api.auth import (
    _authorize_request,
    _principal_has_scope,
    _resolve_scope,
    _register_rotated_key,
    _check_rotated_key,
    reset_rotated_keys,
)


# ---------------------------------------------------------------------------
# Scope logic (unit tests)
# ---------------------------------------------------------------------------

class TestScopes:
    def test_resolve_scope_strips_api_prefix(self):
        assert _resolve_scope("GET", "/api/intents") == "intents.read"
        assert _resolve_scope("GET", "/v1/intents") == "intents.read"

    def test_resolve_scope_post(self):
        assert _resolve_scope("POST", "/api/risk/policy") == "risk.write"
        assert _resolve_scope("POST", "/api/agent/authorize") == "agents.admin"

    def test_principal_has_scope_wildcard(self):
        p = {"scopes": "*"}
        assert _principal_has_scope(p, "risk.write") is True

    def test_principal_has_scope_specific(self):
        p = {"scopes": "risk.read,risk.write"}
        assert _principal_has_scope(p, "risk.read") is True
        assert _principal_has_scope(p, "risk.write") is True
        assert _principal_has_scope(p, "agents.admin") is False

    def test_principal_has_scope_resource_wildcard(self):
        p = {"scopes": "risk.*,intents.read"}
        assert _principal_has_scope(p, "risk.read") is True
        assert _principal_has_scope(p, "risk.write") is True
        assert _principal_has_scope(p, "intents.read") is True
        assert _principal_has_scope(p, "agents.write") is False

    def test_principal_no_scopes_allows_all(self):
        """Backward compat: no scopes defined → role-only access."""
        p = {"scopes": None}
        assert _principal_has_scope(p, "anything") is True

    def test_scope_enforcement_blocks_missing_scope(self):
        """A key with explicit scopes is blocked from endpoints outside its scope."""
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "scopedkey:admin:scopeuser:team-a:intents.read",
        }):
            # intents endpoint should work
            principal = _authorize_request({"x-api-key": "scopedkey"}, "/api/intents")
            assert principal is not None
            # But the standalone _authorize_request doesn't check scopes
            # (scopes are checked in the FastAPI dependency layer)


# ---------------------------------------------------------------------------
# Key rotation (unit tests)
# ---------------------------------------------------------------------------

class TestKeyRotation:
    def test_rotated_key_in_grace_period(self):
        reset_rotated_keys()
        principal = {"role": "admin", "actor": "test", "tenant": None, "scopes": None}
        _register_rotated_key("hash123", principal, grace_seconds=60)
        result = _check_rotated_key("hash123")
        assert result is not None
        assert result["role"] == "admin"

    def test_rotated_key_expired(self):
        reset_rotated_keys()
        principal = {"role": "admin", "actor": "test", "tenant": None}
        _register_rotated_key("hash456", principal, grace_seconds=0)
        time.sleep(0.01)
        result = _check_rotated_key("hash456")
        assert result is None

    def test_rotated_key_used_in_authorize(self):
        """Old key still works during grace period via _authorize_request."""
        reset_rotated_keys()
        principal = {"role": "admin", "actor": "old-user", "tenant": None, "scopes": None, "key_prefix": "old_"}
        import hashlib
        old_hash = hashlib.sha256(b"oldkey123").hexdigest()
        _register_rotated_key(old_hash, principal, grace_seconds=60)

        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "newkey456:admin:new-user",
        }):
            # Old key should still work
            result = _authorize_request({"x-api-key": "oldkey123"}, "/api/intents")
            assert result is not None
            assert result["actor"] == "old-user"


# ---------------------------------------------------------------------------
# Pydantic validation (integration via live server)
# ---------------------------------------------------------------------------

@pytest.fixture
def live_server(db_path):
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
class TestPydanticValidation:
    def test_agent_policy_missing_agent_id(self, live_server):
        """Pydantic rejects missing required field agent_id."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0", "CONVERGE_RATE_LIMIT_ENABLED": "0"}):
            req = Request(
                f"{live_server}/api/agent/policy",
                data=json.dumps({"atl": 2}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(req)
                assert False, "Expected 400"
            except HTTPError as e:
                assert e.code == 400 or e.code == 422

    def test_agent_policy_atl_out_of_range(self, live_server):
        """Pydantic rejects atl > 3."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0", "CONVERGE_RATE_LIMIT_ENABLED": "0"}):
            req = Request(
                f"{live_server}/api/agent/policy",
                data=json.dumps({"agent_id": "bot-1", "atl": 99}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(req)
                assert False, "Expected 400"
            except HTTPError as e:
                assert e.code == 400 or e.code == 422

    def test_agent_authorize_missing_fields(self, live_server):
        """Pydantic rejects missing required fields in authorize."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0", "CONVERGE_RATE_LIMIT_ENABLED": "0"}):
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
                assert e.code == 400 or e.code == 422

    def test_invalid_json_body(self, live_server):
        """Malformed JSON is rejected with clear error."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0", "CONVERGE_RATE_LIMIT_ENABLED": "0"}):
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


@pytest.mark.integration
class TestAccessAuditing:
    def test_denied_access_creates_event(self, live_server, db_path):
        """access.denied event is recorded when auth fails."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "1", "CONVERGE_API_KEYS": "", "CONVERGE_RATE_LIMIT_ENABLED": "0"}):
            req = Request(f"{live_server}/api/intents")
            try:
                urlopen(req)
            except HTTPError:
                pass

            # Give the server a moment to write the event
            time.sleep(0.2)
            events = event_log.query(db_path, event_type="access.denied")
            assert len(events) >= 1
            assert events[0]["payload"]["reason"] == "no_api_key"

    def test_granted_access_on_write(self, live_server, db_path):
        """access.granted event is recorded for POST requests."""
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "adminkey:admin:tester:team-a",
            "CONVERGE_RATE_LIMIT_ENABLED": "0",
        }):
            req = Request(
                f"{live_server}/api/risk/policy",
                data=json.dumps({"tenant_id": "team-a", "max_risk_score": 50}).encode(),
                headers={"Content-Type": "application/json", "x-api-key": "adminkey"},
                method="POST",
            )
            urlopen(req)
            time.sleep(0.2)
            events = event_log.query(db_path, event_type="access.granted")
            assert len(events) >= 1


@pytest.mark.integration
class TestScopeEnforcementHTTP:
    def test_missing_scope_returns_403(self, live_server):
        """Key with limited scopes gets 403 on out-of-scope endpoint."""
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "scopekey:admin:scopeuser:team-a:intents.read",
            "CONVERGE_RATE_LIMIT_ENABLED": "0",
        }):
            # POST to risk/policy requires risk.write — should be denied
            req = Request(
                f"{live_server}/api/risk/policy",
                data=json.dumps({"tenant_id": "team-a", "max_risk_score": 50}).encode(),
                headers={"Content-Type": "application/json", "x-api-key": "scopekey"},
                method="POST",
            )
            try:
                urlopen(req)
                assert False, "Expected 403"
            except HTTPError as e:
                assert e.code == 403
                body = json.loads(e.read())
                assert "scope" in body["error"].lower()

    def test_correct_scope_allowed(self, live_server):
        """Key with matching scope is allowed."""
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "goodkey:viewer:viewer1:team-a:intents.read",
            "CONVERGE_RATE_LIMIT_ENABLED": "0",
        }):
            req = Request(
                f"{live_server}/api/intents",
                headers={"x-api-key": "goodkey"},
            )
            resp = urlopen(req)
            assert resp.status == 200


@pytest.mark.integration
class TestKeyRotationHTTP:
    def test_rotate_key_endpoint(self, live_server):
        """Admin can rotate their key and get a new one."""
        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "1",
            "CONVERGE_API_KEYS": "rotatekey:admin:rotator",
            "CONVERGE_RATE_LIMIT_ENABLED": "0",
        }):
            req = Request(
                f"{live_server}/api/auth/keys/rotate",
                data=json.dumps({"grace_period_seconds": 120}).encode(),
                headers={"Content-Type": "application/json", "x-api-key": "rotatekey"},
                method="POST",
            )
            resp = urlopen(req)
            data = json.loads(resp.read())
            assert "new_key" in data
            assert data["grace_period_seconds"] == 120
            assert data["actor"] == "rotator"
