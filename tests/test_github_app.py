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
    _post_check_run,
    _post_commit_status,
    _token_cache,
    generate_jwt,
    get_installation_token,
    is_configured,
    publish_decision,
    reset_token_cache,
    resolve_installation_id,
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
            42, mock_client, app_id="12345", private_key=_TEST_PEM,
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
            99, mock_client, app_id="12345", private_key=_TEST_PEM,
        )
        # Second call should use cache
        t2 = await get_installation_token(
            99, mock_client, app_id="12345", private_key=_TEST_PEM,
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
            77, mock_client, app_id="12345", private_key=_TEST_PEM,
        )
        assert token == "new_token"
        mock_client.post.assert_called_once()


# ---------------------------------------------------------------------------
# Check-run (internal _post_check_run)
# ---------------------------------------------------------------------------

class TestCheckRun:
    @pytest.mark.asyncio
    async def test_post_check_run(self):
        mock_response = _mock_response(200, {"id": 101, "status": "queued"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await _post_check_run(
            mock_client, "test_token",
            owner="acme", repo="myrepo",
            head_sha="abc123", status="queued", summary="Test run",
        )
        assert result["id"] == 101
        call_args = mock_client.post.call_args
        assert "/repos/acme/myrepo/check-runs" in str(call_args)

    @pytest.mark.asyncio
    async def test_post_check_run_completed(self):
        mock_response = _mock_response(200, {"id": 102, "status": "completed", "conclusion": "success"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await _post_check_run(
            mock_client, "test_token",
            owner="acme", repo="myrepo",
            head_sha="abc123", status="completed",
            conclusion="success", summary="All good",
        )
        assert result["conclusion"] == "success"


# ---------------------------------------------------------------------------
# Commit status (internal _post_commit_status)
# ---------------------------------------------------------------------------

class TestCommitStatus:
    @pytest.mark.asyncio
    async def test_post_commit_status(self):
        mock_response = _mock_response(200, {"state": "success", "id": 55})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await _post_commit_status(
            mock_client, "test_token",
            owner="acme", repo="myrepo",
            sha="def456", state="success",
            description="Validated (risk=12.3)",
        )
        assert result["state"] == "success"

    @pytest.mark.asyncio
    async def test_description_truncated(self):
        mock_response = _mock_response(200, {"state": "failure"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        await _post_commit_status(
            mock_client, "test_token",
            owner="acme", repo="myrepo",
            sha="def456", state="failure",
            description="x" * 200,  # exceeds 140 char limit
        )
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

        result = await publish_decision(
            owner="acme",
            repo="myrepo",
            installation_id=1,
            head_sha="sha456",
            intent_id="acme/myrepo:pr-99",
            decision="blocked",
            reason="Policy blocked: gates [entropy]",
            client=mock_client,
        )
        assert result["decision"] == "blocked"
        assert result["commit_status_state"] == "failure"

    @pytest.mark.asyncio
    async def test_publish_merged(self):
        """Decision 'merged' → check_run completed/success, commit status success."""
        _token_cache[1] = ("test_token", time.time() + 3600)

        check_resp = _mock_response(200, {"id": 203})
        status_resp = _mock_response(200, {"state": "success"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[check_resp, status_resp])

        result = await publish_decision(
            owner="acme", repo="myrepo", installation_id=1,
            head_sha="sha-merged", intent_id="acme/myrepo:pr-50",
            decision="merged", client=mock_client,
        )
        assert result["decision"] == "merged"
        assert result["check_run_id"] == 203
        assert result["commit_status_state"] == "success"
        cr_body = mock_client.post.call_args_list[0].kwargs.get("json", {})
        assert cr_body["status"] == "completed"
        assert cr_body["conclusion"] == "success"

    @pytest.mark.asyncio
    async def test_publish_rejected(self):
        """Decision 'rejected' → check_run completed/failure, commit status failure."""
        _token_cache[1] = ("test_token", time.time() + 3600)

        check_resp = _mock_response(200, {"id": 204})
        status_resp = _mock_response(200, {"state": "failure"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[check_resp, status_resp])

        result = await publish_decision(
            owner="acme", repo="myrepo", installation_id=1,
            head_sha="sha-rejected", intent_id="acme/myrepo:pr-60",
            decision="rejected", reason="Max retries exceeded",
            client=mock_client,
        )
        assert result["decision"] == "rejected"
        assert result["commit_status_state"] == "failure"
        cr_body = mock_client.post.call_args_list[0].kwargs.get("json", {})
        assert cr_body["conclusion"] == "failure"

    @pytest.mark.asyncio
    async def test_publish_pending(self):
        """Decision 'pending' → check_run in_progress (no conclusion), commit status pending."""
        _token_cache[1] = ("test_token", time.time() + 3600)

        check_resp = _mock_response(200, {"id": 205})
        status_resp = _mock_response(200, {"state": "pending"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[check_resp, status_resp])

        result = await publish_decision(
            owner="acme", repo="myrepo", installation_id=1,
            head_sha="sha-pending", intent_id="acme/myrepo:pr-70",
            decision="pending", reason="Re-push detected, revalidating",
            client=mock_client,
        )
        assert result["decision"] == "pending"
        assert result["commit_status_state"] == "pending"
        cr_body = mock_client.post.call_args_list[0].kwargs.get("json", {})
        assert cr_body["status"] == "in_progress"
        assert "conclusion" not in cr_body

    @pytest.mark.asyncio
    async def test_publish_raises_on_api_error(self):
        """publish_decision propagates exceptions — callers handle errors."""
        _token_cache[1] = ("test_token", time.time() + 3600)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=MagicMock(status_code=500),
        ))

        with pytest.raises(httpx.HTTPStatusError):
            await publish_decision(
                owner="acme",
                repo="myrepo",
                installation_id=1,
                head_sha="sha789",
                intent_id="test-1",
                decision="validated",
                client=mock_client,
            )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class TestIsConfigured:
    def test_configured_when_app_id_set(self):
        with patch.dict(os.environ, {"CONVERGE_GITHUB_APP_ID": "123"}):
            assert is_configured() is True

    def test_not_configured_when_app_id_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CONVERGE_GITHUB_APP_ID", None)
            assert is_configured() is False


class TestResolveInstallationId:
    def test_per_intent_preferred(self):
        assert resolve_installation_id(77777, 999) == 77777

    def test_fallback_when_per_intent_none(self):
        assert resolve_installation_id(None, 999) == 999

    def test_fallback_when_per_intent_empty(self):
        assert resolve_installation_id("", 999) == 999

    def test_fallback_when_per_intent_invalid(self):
        assert resolve_installation_id("not-a-number", 999) == 999

    def test_env_var_when_no_explicit_values(self):
        with patch.dict(os.environ, {"CONVERGE_GITHUB_INSTALLATION_ID": "555"}):
            assert resolve_installation_id() == 555

    def test_zero_when_nothing_valid(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CONVERGE_GITHUB_INSTALLATION_ID", None)
            assert resolve_installation_id() == 0

    def test_negative_rejected(self):
        assert resolve_installation_id(-5, -10) == 0

    def test_string_numeric_accepted(self):
        assert resolve_installation_id("42") == 42


# ---------------------------------------------------------------------------
# Publish mode
# ---------------------------------------------------------------------------

class TestPublishMode:
    @pytest.mark.asyncio
    async def test_checks_only_skips_commit_status(self):
        """publish_mode=checks: only creates check-run, not commit status."""
        _token_cache[1] = ("test_token", time.time() + 3600)

        check_resp = _mock_response(200, {"id": 300})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=check_resp)

        with patch.dict(os.environ, {"CONVERGE_GITHUB_PUBLISH_MODE": "checks"}):
            result = await publish_decision(
                owner="acme", repo="myrepo", installation_id=1,
                head_sha="sha-mode", intent_id="mode-test",
                decision="validated", client=mock_client,
            )
        assert result["check_run_id"] == 300
        assert "commit_status_state" not in result
        assert mock_client.post.call_count == 1  # only check-run

    @pytest.mark.asyncio
    async def test_status_only_skips_check_run(self):
        """publish_mode=status: only creates commit status, not check-run."""
        _token_cache[1] = ("test_token", time.time() + 3600)

        status_resp = _mock_response(200, {"state": "success"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=status_resp)

        with patch.dict(os.environ, {"CONVERGE_GITHUB_PUBLISH_MODE": "status"}):
            result = await publish_decision(
                owner="acme", repo="myrepo", installation_id=1,
                head_sha="sha-mode", intent_id="mode-test",
                decision="validated", client=mock_client,
            )
        assert "check_run_id" not in result
        assert result["commit_status_state"] == "success"
        assert mock_client.post.call_count == 1  # only commit status

    @pytest.mark.asyncio
    async def test_both_publishes_both(self):
        """publish_mode=both (default): creates check-run AND commit status."""
        _token_cache[1] = ("test_token", time.time() + 3600)

        check_resp = _mock_response(200, {"id": 301})
        status_resp = _mock_response(200, {"state": "success"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[check_resp, status_resp])

        with patch.dict(os.environ, {"CONVERGE_GITHUB_PUBLISH_MODE": "both"}):
            result = await publish_decision(
                owner="acme", repo="myrepo", installation_id=1,
                head_sha="sha-mode", intent_id="mode-test",
                decision="validated", client=mock_client,
            )
        assert result["check_run_id"] == 301
        assert result["commit_status_state"] == "success"
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_invalid_mode_defaults_to_both(self):
        """Invalid publish mode falls back to 'both'."""
        _token_cache[1] = ("test_token", time.time() + 3600)

        check_resp = _mock_response(200, {"id": 302})
        status_resp = _mock_response(200, {"state": "success"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=[check_resp, status_resp])

        with patch.dict(os.environ, {"CONVERGE_GITHUB_PUBLISH_MODE": "garbage"}):
            result = await publish_decision(
                owner="acme", repo="myrepo", installation_id=1,
                head_sha="sha-mode", intent_id="mode-test",
                decision="validated", client=mock_client,
            )
        assert result["check_run_id"] == 302
        assert mock_client.post.call_count == 2
