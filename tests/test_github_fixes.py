"""Targeted tests for GitHub integration fixes (P1/P2 from diagnostic review).

P1: httpx import in worker GitHub publish path
P1: push webhook filters by repo (multi-repo safety)
P2: per-intent installation_id preferred over global ENV
P2: webhook endpoint exempt from rate limiting
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from converge import event_log
from converge.models import Event, EventType, Intent, Status


# ---------------------------------------------------------------------------
# P1: httpx import available in worker
# ---------------------------------------------------------------------------

class TestWorkerHttpxImport:
    def test_httpx_importable_from_worker_module(self):
        """worker.py has httpx in its namespace (fix for NameError at line 176)."""
        import converge.worker as worker_mod
        assert hasattr(worker_mod, "httpx"), "httpx should be imported in worker.py"

    def test_worker_async_publish_uses_httpx(self, db_path):
        """_async_publish creates an httpx.AsyncClient without NameError."""
        import asyncio
        from converge.worker import QueueWorker, WorkerConfig

        config = WorkerConfig()
        config.db_path = str(db_path)
        config.github_app_id = "123"
        config.github_installation_id = "456"
        worker = QueueWorker(config)

        # Mock publish_decision so we don't make real HTTP calls,
        # but the httpx.AsyncClient creation must not raise NameError
        with patch("converge.integrations.github_app.publish_decision", new_callable=AsyncMock):
            # Empty results → no iteration, but httpx.AsyncClient() must resolve
            asyncio.run(worker._async_publish([]))
            # If httpx was missing, this would raise NameError


# ---------------------------------------------------------------------------
# P1: push webhook multi-repo filtering
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


def _webhook(url: str, event: str, payload: dict, delivery_id: str = "d-1") -> dict:
    req = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery_id,
        },
        method="POST",
    )
    resp = urlopen(req)
    return json.loads(resp.read())


@pytest.mark.integration
class TestPushMultiRepoFilter:
    def test_push_does_not_revalidate_other_repo_intent(self, live_server, db_path):
        """Push in repo-A must NOT revalidate an intent from repo-B with same branch name."""
        # Create intent for repo-B
        intent_b = Intent(
            id="org/repo-B:pr-10",
            source="feature/shared-name",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            technical={"repo": "org/repo-B", "initial_base_commit": "old-sha"},
        )
        event_log.upsert_intent(db_path, intent_b)

        # Push on repo-A with the same branch name
        result = _webhook(
            f"{live_server}/integrations/github/webhook",
            "push",
            {
                "ref": "refs/heads/feature/shared-name",
                "after": "new-sha-A",
                "repository": {"full_name": "org/repo-A"},
            },
            delivery_id="d-push-cross-repo",
        )
        assert result["revalidated"] == [], "Intent from repo-B should NOT be revalidated by push on repo-A"

        # Verify intent-B was NOT touched
        updated_b = event_log.get_intent(db_path, "org/repo-B:pr-10")
        assert updated_b.status == Status.VALIDATED
        assert updated_b.technical["initial_base_commit"] == "old-sha"

    def test_push_revalidates_same_repo_intent(self, live_server, db_path):
        """Push in repo-A DOES revalidate intents that belong to repo-A."""
        intent = Intent(
            id="org/repo-A:pr-20",
            source="feature/my-branch",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            technical={"repo": "org/repo-A", "initial_base_commit": "old-sha"},
        )
        event_log.upsert_intent(db_path, intent)

        result = _webhook(
            f"{live_server}/integrations/github/webhook",
            "push",
            {
                "ref": "refs/heads/feature/my-branch",
                "after": "new-sha-A",
                "repository": {"full_name": "org/repo-A"},
            },
            delivery_id="d-push-same-repo",
        )
        assert "org/repo-A:pr-20" in result["revalidated"]

    def test_push_revalidates_intent_without_repo(self, live_server, db_path):
        """Intents created without repo metadata (legacy) are still revalidated."""
        intent = Intent(
            id="legacy-intent-1",
            source="feature/legacy",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            technical={"initial_base_commit": "old-sha"},
        )
        event_log.upsert_intent(db_path, intent)

        result = _webhook(
            f"{live_server}/integrations/github/webhook",
            "push",
            {
                "ref": "refs/heads/feature/legacy",
                "after": "new-sha",
                "repository": {"full_name": "any/repo"},
            },
            delivery_id="d-push-legacy",
        )
        assert "legacy-intent-1" in result["revalidated"]


# ---------------------------------------------------------------------------
# P2: per-intent installation_id
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPerIntentInstallationId:
    def test_pr_opened_stores_installation_id(self, live_server, db_path):
        """PR opened webhook stores installation.id in intent.technical."""
        _webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            {
                "action": "opened",
                "pull_request": {
                    "number": 100,
                    "title": "Install ID test",
                    "head": {"ref": "feature/install-test", "sha": "sha100"},
                    "base": {"ref": "main"},
                },
                "repository": {"full_name": "acme/install-repo"},
                "installation": {"id": 77777},
            },
            delivery_id="d-install-100",
        )

        intent = event_log.get_intent(db_path, "acme/install-repo:pr-100")
        assert intent is not None
        assert intent.technical.get("installation_id") == 77777

    def test_try_publish_decision_uses_intent_installation_id(self):
        """_try_publish_decision prefers the passed installation_id over ENV."""
        from converge.api.routers.webhooks import _try_publish_decision
        import asyncio

        mock_pub = AsyncMock()

        with patch.dict(os.environ, {
            "CONVERGE_GITHUB_APP_ID": "111",
            "CONVERGE_GITHUB_INSTALLATION_ID": "999",  # global default
        }):
            # Patch at the source — the lazy import resolves from this module
            with patch("converge.integrations.github_app.publish_decision", mock_pub):
                asyncio.run(_try_publish_decision(
                    repo_full_name="acme/repo",
                    head_sha="sha-abc",
                    intent_id="test-intent",
                    decision="validated",
                    installation_id=77777,  # per-intent value
                ))
                assert mock_pub.called, "publish_decision should have been called"
                call_kwargs = mock_pub.call_args.kwargs
                assert call_kwargs["installation_id"] == 77777, \
                    "Should use per-intent installation_id, not global ENV (999)"


# ---------------------------------------------------------------------------
# P2: webhook exempt from rate limiting
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestWebhookRateLimitExempt:
    def test_webhook_not_rate_limited(self, db_path):
        """Webhook endpoint is exempt from rate limiting even under burst."""
        import uvicorn
        from converge.api import create_app
        from converge.api.rate_limit import reset_limiter

        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "0",
            "CONVERGE_RATE_LIMIT_ENABLED": "1",
            "CONVERGE_RATE_LIMIT_RPM": "2",  # very low — 2 req/min
        }):
            reset_limiter()
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

            try:
                base = f"http://127.0.0.1:{port}"

                # Send 5 webhook requests (well over the 2 RPM limit)
                for i in range(5):
                    result = _webhook(
                        f"{base}/integrations/github/webhook",
                        "ping",
                        {"zen": "test"},
                        delivery_id=f"rate-test-{i}",
                    )
                    assert result.get("ok") is True, f"Webhook request {i} should not be rate-limited"

                # Verify that regular API endpoint IS rate-limited
                for _ in range(3):
                    urlopen(f"{base}/health/live")
                # health is exempt too, try /api/intents
                # (first 2 pass, 3rd should get 429)
                got_429 = False
                for _ in range(5):
                    try:
                        urlopen(f"{base}/api/intents")
                    except HTTPError as e:
                        if e.code == 429:
                            got_429 = True
                            break
                assert got_429, "Regular API endpoint should be rate-limited at 2 RPM"
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                reset_limiter()
