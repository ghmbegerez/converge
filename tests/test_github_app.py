"""S5 tests: GitHub App integration — JWT, tokens, check-runs, commit status, publishing."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt
import pytest

from converge.integrations.github_app import (
    _token_cache,
    create_check_run,
    create_commit_status,
    generate_jwt,
    get_installation_token,
    publish_decision,
    reset_token_cache,
    update_check_run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Generate a real RSA key pair for tests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_test_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_PEM = _test_private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_TEST_PUBLIC_KEY = _test_private_key.public_key()


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_token_cache()
    yield
    reset_token_cache()


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    """Create an httpx.Response with a request attached (needed for raise_for_status)."""
    return httpx.Response(
        status_code,
        json=json_data,
        request=httpx.Request("POST", "https://api.github.com/test"),
    )


# ---------------------------------------------------------------------------
# JWT generation
# ---------------------------------------------------------------------------

class TestJWT:
    def test_generate_jwt_returns_valid_token(self):
        token = generate_jwt(app_id="12345", private_key=_TEST_PEM)
        decoded = jwt.decode(token, _TEST_PUBLIC_KEY, algorithms=["RS256"])
        assert decoded["iss"] == "12345"
        assert "exp" in decoded
        assert "iat" in decoded

    def test_jwt_expires_in_10_minutes(self):
        token = generate_jwt(app_id="12345", private_key=_TEST_PEM)
        decoded = jwt.decode(token, _TEST_PUBLIC_KEY, algorithms=["RS256"])
        # exp should be ~10 min from now
        assert decoded["exp"] - decoded["iat"] <= 660 + 60  # 10min + 60s skew

    def test_jwt_from_env(self):
        with patch.dict(os.environ, {
            "CONVERGE_GITHUB_APP_ID": "99",
            "CONVERGE_GITHUB_APP_PRIVATE_KEY": _TEST_PEM,
        }):
            token = generate_jwt()
            decoded = jwt.decode(token, _TEST_PUBLIC_KEY, algorithms=["RS256"])
            assert decoded["iss"] == "99"

    def test_jwt_missing_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CONVERGE_GITHUB_APP_PRIVATE_KEY_PATH", None)
            os.environ.pop("CONVERGE_GITHUB_APP_PRIVATE_KEY", None)
            with pytest.raises(RuntimeError, match="private key not configured"):
                generate_jwt(app_id="1")


# ---------------------------------------------------------------------------
# Installation token
# ---------------------------------------------------------------------------

class TestInstallationToken:
    @pytest.mark.asyncio
    async def test_get_installation_token(self):
        mock_response = _mock_response(200, {"token": "ghs_test_token_123", "expires_at": "2099-01-01T00:00:00Z"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        token = await get_installation_token(
            42,
            client=mock_client,
            app_id="12345",
            private_key=_TEST_PEM,
        )
        assert token == "ghs_test_token_123"
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_token_caching(self):
        mock_response = _mock_response(200, {"token": "cached_token"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        # First call fetches
        t1 = await get_installation_token(
            99, client=mock_client, app_id="12345", private_key=_TEST_PEM,
        )
        # Second call should use cache
        t2 = await get_installation_token(
            99, client=mock_client, app_id="12345", private_key=_TEST_PEM,
        )
        assert t1 == t2 == "cached_token"
        assert mock_client.post.call_count == 1  # only 1 HTTP call

    @pytest.mark.asyncio
    async def test_token_refresh_on_expiry(self):
        # Pre-seed cache with expired token
        _token_cache[77] = ("old_token", time.time() - 100)

        mock_response = _mock_response(200, {"token": "new_token"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        token = await get_installation_token(
            77, client=mock_client, app_id="12345", private_key=_TEST_PEM,
        )
        assert token == "new_token"
        mock_client.post.assert_called_once()


# ---------------------------------------------------------------------------
# Check-run
# ---------------------------------------------------------------------------

class TestCheckRun:
    @pytest.mark.asyncio
    async def test_create_check_run(self):
        # Seed token cache to skip JWT exchange
        _token_cache[1] = ("test_token", time.time() + 3600)

        mock_response = _mock_response(200, {"id": 101, "status": "queued"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await create_check_run(
            owner="acme",
            repo="myrepo",
            installation_id=1,
            head_sha="abc123",
            status="queued",
            summary="Test run",
            client=mock_client,
            app_id="12345",
            private_key=_TEST_PEM,
        )
        assert result["id"] == 101

        # Verify correct URL called
        call_args = mock_client.post.call_args
        assert "/repos/acme/myrepo/check-runs" in str(call_args)

    @pytest.mark.asyncio
    async def test_create_check_run_completed(self):
        _token_cache[1] = ("test_token", time.time() + 3600)

        mock_response = _mock_response(200, {"id": 102, "status": "completed", "conclusion": "success"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await create_check_run(
            owner="acme",
            repo="myrepo",
            installation_id=1,
            head_sha="abc123",
            status="completed",
            conclusion="success",
            summary="All good",
            client=mock_client,
            app_id="12345",
            private_key=_TEST_PEM,
        )
        assert result["conclusion"] == "success"

    @pytest.mark.asyncio
    async def test_update_check_run(self):
        _token_cache[1] = ("test_token", time.time() + 3600)

        mock_response = _mock_response(200, {"id": 101, "status": "completed", "conclusion": "failure"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.patch = AsyncMock(return_value=mock_response)

        result = await update_check_run(
            owner="acme",
            repo="myrepo",
            installation_id=1,
            check_run_id=101,
            status="completed",
            conclusion="failure",
            client=mock_client,
            app_id="12345",
            private_key=_TEST_PEM,
        )
        assert result["conclusion"] == "failure"


# ---------------------------------------------------------------------------
# Commit status
# ---------------------------------------------------------------------------

class TestCommitStatus:
    @pytest.mark.asyncio
    async def test_create_commit_status(self):
        _token_cache[1] = ("test_token", time.time() + 3600)

        mock_response = _mock_response(200, {"state": "success", "id": 55})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await create_commit_status(
            owner="acme",
            repo="myrepo",
            installation_id=1,
            sha="def456",
            state="success",
            description="Validated (risk=12.3)",
            client=mock_client,
            app_id="12345",
            private_key=_TEST_PEM,
        )
        assert result["state"] == "success"

    @pytest.mark.asyncio
    async def test_description_truncated(self):
        _token_cache[1] = ("test_token", time.time() + 3600)

        mock_response = _mock_response(200, {"state": "failure"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        await create_commit_status(
            owner="acme",
            repo="myrepo",
            installation_id=1,
            sha="def456",
            state="failure",
            description="x" * 200,  # exceeds 140 char limit
            client=mock_client,
            app_id="12345",
            private_key=_TEST_PEM,
        )
        # Verify description was truncated in the request body
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json", {})
        assert len(body["description"]) == 140


# ---------------------------------------------------------------------------
# publish_decision (high-level)
# ---------------------------------------------------------------------------

class TestPublishDecision:
    @pytest.mark.asyncio
    async def test_publish_validated(self):
        _token_cache[1] = ("test_token", time.time() + 3600)

        check_resp = _mock_response(200, {"id": 200})
        status_resp = _mock_response(200, {"state": "success"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[check_resp, status_resp])
        mock_client.aclose = AsyncMock()

        result = await publish_decision(
            owner="acme",
            repo="myrepo",
            installation_id=1,
            head_sha="sha123",
            intent_id="acme/myrepo:pr-42",
            decision="validated",
            trace_id="trace-abc",
            risk_score=15.5,
            client=mock_client,
            app_id="12345",
            private_key=_TEST_PEM,
        )
        assert result["decision"] == "validated"
        assert result["check_run_id"] == 200
        assert result["commit_status_state"] == "success"

    @pytest.mark.asyncio
    async def test_publish_blocked(self):
        _token_cache[1] = ("test_token", time.time() + 3600)

        check_resp = _mock_response(200, {"id": 201})
        status_resp = _mock_response(200, {"state": "failure"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[check_resp, status_resp])
        mock_client.aclose = AsyncMock()

        result = await publish_decision(
            owner="acme",
            repo="myrepo",
            installation_id=1,
            head_sha="sha456",
            intent_id="acme/myrepo:pr-99",
            decision="blocked",
            reason="Policy blocked: gates [entropy]",
            client=mock_client,
            app_id="12345",
            private_key=_TEST_PEM,
        )
        assert result["decision"] == "blocked"
        assert result["commit_status_state"] == "failure"

    @pytest.mark.asyncio
    async def test_publish_handles_error_gracefully(self):
        _token_cache[1] = ("test_token", time.time() + 3600)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=MagicMock(status_code=500),
        ))
        mock_client.aclose = AsyncMock()

        result = await publish_decision(
            owner="acme",
            repo="myrepo",
            installation_id=1,
            head_sha="sha789",
            intent_id="test-1",
            decision="validated",
            client=mock_client,
            app_id="12345",
            private_key=_TEST_PEM,
        )
        assert result["error"] == "publish_failed"
