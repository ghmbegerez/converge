"""Tests for GitHub Merge Queue (merge_group webhook) integration."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from unittest.mock import patch
from urllib.request import Request, urlopen

import pytest

from converge import event_log
from converge.models import Status


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


def _webhook(url: str, payload: dict, delivery_id: str = "mg-1") -> dict:
    req = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "merge_group",
            "X-GitHub-Delivery": delivery_id,
        },
        method="POST",
    )
    resp = urlopen(req)
    return json.loads(resp.read())


# ---------------------------------------------------------------------------
# checks_requested
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMergeGroupChecksRequested:

    def test_checks_requested_creates_intent(self, live_server, db_path):
        """merge_group checks_requested creates a READY intent."""
        result = _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "checks_requested",
                "merge_group": {
                    "head_ref": "refs/heads/gh-readonly-queue/main/pr-42-abc123",
                    "head_sha": "abc123def456",
                    "base_ref": "refs/heads/main",
                    "base_sha": "xyz789000000",
                },
                "repository": {"full_name": "acme/backend"},
                "installation": {"id": 77777},
            },
            delivery_id="mg-cr-1",
        )

        assert result["ok"] is True
        assert result["action"] == "merge_group_checks_requested"
        assert result["intent_id"] == "acme/backend:mg-abc123def456"

        intent = event_log.get_intent(db_path, "acme/backend:mg-abc123def456")
        assert intent is not None
        assert intent.status == Status.READY
        assert intent.created_by == "github-merge-queue"
        assert intent.target == "main"
        assert intent.technical["repo"] == "acme/backend"
        assert intent.technical["initial_base_commit"] == "abc123def456"
        assert intent.technical["webhook_event"] == "merge_group"

    def test_checks_requested_stores_installation_id(self, live_server, db_path):
        """installation_id from webhook is stored in intent.technical."""
        _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "checks_requested",
                "merge_group": {
                    "head_ref": "refs/heads/gh-readonly-queue/main/pr-99-def",
                    "head_sha": "def456789012",
                    "base_ref": "refs/heads/main",
                    "base_sha": "base000000",
                },
                "repository": {"full_name": "acme/backend"},
                "installation": {"id": 88888},
            },
            delivery_id="mg-inst-1",
        )

        intent = event_log.get_intent(db_path, "acme/backend:mg-def456789012")
        assert intent.technical["installation_id"] == 88888

    def test_checks_requested_stores_merge_group_ref(self, live_server, db_path):
        """merge_group head_ref is stored in technical.merge_group_head_ref."""
        head_ref = "refs/heads/gh-readonly-queue/main/pr-55-xyz"
        _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "checks_requested",
                "merge_group": {
                    "head_ref": head_ref,
                    "head_sha": "xyz999888777",
                    "base_ref": "refs/heads/develop",
                    "base_sha": "base111222",
                },
                "repository": {"full_name": "acme/frontend"},
                "installation": {"id": 11111},
            },
            delivery_id="mg-ref-1",
        )

        intent = event_log.get_intent(db_path, "acme/frontend:mg-xyz999888777")
        assert intent.technical["merge_group_head_ref"] == head_ref
        assert intent.target == "develop"

    def test_checks_requested_idempotent(self, live_server):
        """Same delivery_id returns duplicate=true on replay."""
        payload = {
            "action": "checks_requested",
            "merge_group": {
                "head_ref": "refs/heads/gh-readonly-queue/main/pr-1-aaa",
                "head_sha": "aaa111222333",
                "base_ref": "refs/heads/main",
                "base_sha": "base000",
            },
            "repository": {"full_name": "acme/repo"},
            "installation": {"id": 1},
        }
        url = f"{live_server}/integrations/github/webhook"

        r1 = _webhook(url, payload, delivery_id="mg-idem-1")
        assert r1["ok"] is True
        assert "duplicate" not in r1

        r2 = _webhook(url, payload, delivery_id="mg-idem-1")
        assert r2["ok"] is True
        assert r2["duplicate"] is True


# ---------------------------------------------------------------------------
# destroyed
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMergeGroupDestroyed:

    def test_destroyed_rejects_intent(self, live_server, db_path):
        """merge_group destroyed marks existing intent as REJECTED."""
        # First, create via checks_requested
        _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "checks_requested",
                "merge_group": {
                    "head_ref": "refs/heads/gh-readonly-queue/main/pr-10-bbb",
                    "head_sha": "bbb444555666",
                    "base_ref": "refs/heads/main",
                    "base_sha": "base999",
                },
                "repository": {"full_name": "acme/api"},
                "installation": {"id": 22222},
            },
            delivery_id="mg-d-create",
        )

        intent = event_log.get_intent(db_path, "acme/api:mg-bbb444555666")
        assert intent.status == Status.READY

        # Now destroy it
        result = _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "destroyed",
                "merge_group": {
                    "head_ref": "refs/heads/gh-readonly-queue/main/pr-10-bbb",
                    "head_sha": "bbb444555666",
                    "base_ref": "refs/heads/main",
                    "base_sha": "base999",
                },
                "repository": {"full_name": "acme/api"},
                "reason": "checks_failure",
            },
            delivery_id="mg-d-destroy",
        )

        assert result["ok"] is True
        assert result["action"] == "merge_group_destroyed"

        intent = event_log.get_intent(db_path, "acme/api:mg-bbb444555666")
        assert intent.status == Status.REJECTED

    def test_destroyed_unknown_intent_ignored(self, live_server):
        """destroyed for unknown intent does not fail."""
        result = _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "destroyed",
                "merge_group": {
                    "head_ref": "refs/heads/gh-readonly-queue/main/pr-999-zzz",
                    "head_sha": "zzz000111222",
                    "base_ref": "refs/heads/main",
                    "base_sha": "base000",
                },
                "repository": {"full_name": "acme/unknown"},
                "reason": "dequeued",
            },
            delivery_id="mg-d-unknown",
        )

        assert result["ok"] is True
        assert result["action"] == "ignored"
        assert result["reason"] == "unknown_intent"

    def test_destroyed_includes_reason_in_event(self, live_server, db_path):
        """reason from destroyed payload is recorded in the event."""
        # Create first
        _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "checks_requested",
                "merge_group": {
                    "head_ref": "refs/heads/gh-readonly-queue/main/pr-77-ccc",
                    "head_sha": "ccc777888999",
                    "base_ref": "refs/heads/main",
                    "base_sha": "base777",
                },
                "repository": {"full_name": "acme/service"},
                "installation": {"id": 33333},
            },
            delivery_id="mg-reason-create",
        )

        # Destroy with reason
        _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "destroyed",
                "merge_group": {
                    "head_ref": "refs/heads/gh-readonly-queue/main/pr-77-ccc",
                    "head_sha": "ccc777888999",
                    "base_ref": "refs/heads/main",
                    "base_sha": "base777",
                },
                "repository": {"full_name": "acme/service"},
                "reason": "merge_conflict",
            },
            delivery_id="mg-reason-destroy",
        )

        events = event_log.query(db_path, event_type="merge_group.destroyed")
        assert len(events) >= 1
        payload = events[-1]["payload"]
        assert payload["reason"] == "merge_conflict"
        assert payload["trigger"] == "github_merge_group_destroyed"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMergeGroupEdgeCases:

    def test_incomplete_payload_ignored(self, live_server):
        """Payload without merge_group or head_sha returns ignored."""
        result = _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "checks_requested",
                "merge_group": {},
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="mg-edge-empty",
        )

        assert result["ok"] is True
        assert result["action"] == "ignored"
        assert result["reason"] == "incomplete_payload"

    def test_unknown_action_ignored(self, live_server):
        """Unknown merge_group action does not fail."""
        result = _webhook(
            f"{live_server}/integrations/github/webhook",
            {
                "action": "some_future_action",
                "merge_group": {
                    "head_ref": "refs/heads/gh-readonly-queue/main/pr-1-x",
                    "head_sha": "fff111222333",
                    "base_ref": "refs/heads/main",
                    "base_sha": "base000",
                },
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="mg-edge-unknown",
        )

        assert result["ok"] is True
        assert result["action"] == "ignored"
        assert "unknown_merge_group_action" in result["reason"]
