"""S5 tests: worker lifecycle, graceful shutdown, webhook sync, push revalidation."""

from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
from unittest.mock import patch, MagicMock
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from converge import event_log
from converge.models import Event, EventType, Intent, Status
from converge.worker import QueueWorker, WorkerConfig


# ---------------------------------------------------------------------------
# Worker unit tests
# ---------------------------------------------------------------------------

class TestWorkerConfig:
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            # Need to avoid reading stale env vars
            cfg = WorkerConfig()
            assert cfg.poll_interval == 5
            assert cfg.batch_size == 20
            assert cfg.max_retries == 3
            assert cfg.target == "main"
            assert cfg.auto_confirm is False
            assert cfg.github_enabled is False

    def test_custom_config(self):
        with patch.dict(os.environ, {
            "CONVERGE_WORKER_POLL_INTERVAL": "10",
            "CONVERGE_WORKER_BATCH_SIZE": "50",
            "CONVERGE_WORKER_MAX_RETRIES": "5",
            "CONVERGE_WORKER_TARGET": "develop",
            "CONVERGE_WORKER_AUTO_CONFIRM": "1",
            "CONVERGE_GITHUB_APP_ID": "123",
            "CONVERGE_GITHUB_INSTALLATION_ID": "456",
        }):
            cfg = WorkerConfig()
            assert cfg.poll_interval == 10
            assert cfg.batch_size == 50
            assert cfg.max_retries == 5
            assert cfg.target == "develop"
            assert cfg.auto_confirm is True
            assert cfg.github_enabled is True


class TestWorkerLifecycle:
    def test_worker_starts_and_stops(self, db_path):
        """Worker can be started in a thread and stopped gracefully."""
        config = WorkerConfig()
        config.db_path = str(db_path)
        config.poll_interval = 1

        worker = QueueWorker(config)

        def run():
            worker.start()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        # Wait for at least one cycle
        time.sleep(0.3)
        assert worker.is_running is True

        worker.stop()
        thread.join(timeout=5)
        assert worker.is_running is False
        assert worker.cycles >= 1

    def test_worker_processes_empty_queue(self, db_path):
        """Worker handles empty queue gracefully."""
        config = WorkerConfig()
        config.db_path = str(db_path)
        config.poll_interval = 1

        worker = QueueWorker(config)

        def run():
            worker.start()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        time.sleep(0.3)
        worker.stop()
        thread.join(timeout=5)

        assert worker.total_processed == 0

    def test_worker_records_start_stop_events(self, db_path):
        """Worker records WORKER_STARTED and WORKER_STOPPED events."""
        config = WorkerConfig()
        config.db_path = str(db_path)
        config.poll_interval = 1

        worker = QueueWorker(config)

        def run():
            worker.start()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        time.sleep(0.3)
        worker.stop()
        thread.join(timeout=5)

        started = event_log.query(db_path, event_type=EventType.WORKER_STARTED)
        stopped = event_log.query(db_path, event_type=EventType.WORKER_STOPPED)
        assert len(started) >= 1
        assert len(stopped) >= 1
        assert started[0]["payload"]["pid"] == os.getpid()

    def test_worker_stop_method(self, db_path):
        """Calling stop() triggers graceful shutdown."""
        config = WorkerConfig()
        config.db_path = str(db_path)
        config.poll_interval = 60  # long interval so it's waiting

        worker = QueueWorker(config)

        def run():
            worker.start()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        time.sleep(0.2)

        # stop() should gracefully shut down the worker
        worker.stop()
        thread.join(timeout=5)
        assert worker.is_running is False
        assert worker._draining is True


# ---------------------------------------------------------------------------
# Webhook sync tests (integration via live server)
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


@pytest.mark.integration
class TestWebhookSync:
    def _webhook(self, url: str, event: str, payload: dict, delivery_id: str = "d-1") -> dict:
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

    def test_pr_opened_creates_intent(self, live_server, db_path):
        """PR opened webhook creates intent in READY state."""
        result = self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            {
                "action": "opened",
                "pull_request": {
                    "number": 42,
                    "title": "Add login",
                    "head": {"ref": "feature/login", "sha": "abc123"},
                    "base": {"ref": "main", "sha": "def456"},
                },
                "repository": {"full_name": "acme/myrepo"},
            },
        )
        assert result["ok"] is True
        assert result["intent_id"] == "acme/myrepo:pr-42"

        intent = event_log.get_intent(db_path, "acme/myrepo:pr-42")
        assert intent is not None
        assert intent.status == Status.READY
        assert intent.source == "feature/login"

    def test_pr_closed_merged_updates_intent(self, live_server, db_path):
        """PR merged webhook updates intent to MERGED."""
        # First create the intent via PR opened
        self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            {
                "action": "opened",
                "pull_request": {
                    "number": 43,
                    "title": "Refactor",
                    "head": {"ref": "feature/refactor", "sha": "aaa"},
                    "base": {"ref": "main"},
                },
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="d-open-43",
        )

        # Now close with merge
        result = self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            {
                "action": "closed",
                "pull_request": {
                    "number": 43,
                    "merged": True,
                    "merge_commit_sha": "merge-abc",
                    "head": {"ref": "feature/refactor", "sha": "aaa"},
                    "base": {"ref": "main"},
                },
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="d-close-43",
        )
        assert result["action"] == "merged"

        intent = event_log.get_intent(db_path, "acme/repo:pr-43")
        assert intent.status == Status.MERGED

    def test_pr_closed_not_merged_rejects_intent(self, live_server, db_path):
        """PR closed without merge → intent rejected."""
        self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            {
                "action": "opened",
                "pull_request": {
                    "number": 44,
                    "title": "WIP",
                    "head": {"ref": "wip/stuff", "sha": "bbb"},
                    "base": {"ref": "main"},
                },
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="d-open-44",
        )

        result = self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            {
                "action": "closed",
                "pull_request": {
                    "number": 44,
                    "merged": False,
                    "head": {"ref": "wip/stuff", "sha": "bbb"},
                    "base": {"ref": "main"},
                },
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="d-close-44",
        )
        assert result["action"] == "rejected"

        intent = event_log.get_intent(db_path, "acme/repo:pr-44")
        assert intent.status == Status.REJECTED

    def test_push_triggers_revalidation(self, live_server, db_path):
        """Push on source branch resets associated intent to READY."""
        # Create an intent that is VALIDATED
        intent = Intent(
            id="acme/repo:pr-50",
            source="feature/push-test",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            tenant_id=None,
            technical={"repo": "acme/repo", "initial_base_commit": "old-sha"},
        )
        event_log.upsert_intent(db_path, intent)

        result = self._webhook(
            f"{live_server}/integrations/github/webhook",
            "push",
            {
                "ref": "refs/heads/feature/push-test",
                "after": "new-sha-999",
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="d-push-50",
        )
        assert result["action"] == "push_processed"
        assert "acme/repo:pr-50" in result["revalidated"]

        updated = event_log.get_intent(db_path, "acme/repo:pr-50")
        assert updated.status == Status.READY
        assert updated.technical["initial_base_commit"] == "new-sha-999"

    def test_pr_synchronize_updates_sha(self, live_server, db_path):
        """PR synchronize (force-push) updates head SHA and resets to READY."""
        self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            {
                "action": "opened",
                "pull_request": {
                    "number": 55,
                    "title": "Feature",
                    "head": {"ref": "feature/sync", "sha": "sha-v1"},
                    "base": {"ref": "main"},
                },
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="d-open-55",
        )

        # Force-push (synchronize)
        result = self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            {
                "action": "synchronize",
                "pull_request": {
                    "number": 55,
                    "title": "Feature",
                    "head": {"ref": "feature/sync", "sha": "sha-v2"},
                    "base": {"ref": "main"},
                },
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="d-sync-55",
        )
        assert result["ok"] is True

        intent = event_log.get_intent(db_path, "acme/repo:pr-55")
        assert intent.status == Status.READY
        assert intent.technical["initial_base_commit"] == "sha-v2"

    def test_idempotency_duplicate_delivery(self, live_server, db_path):
        """Duplicate delivery_id is detected and ignored."""
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 60,
                "title": "Dup test",
                "head": {"ref": "dup/branch", "sha": "dup-sha"},
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "acme/repo"},
        }

        result1 = self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            payload,
            delivery_id="dup-delivery-1",
        )
        assert result1["ok"] is True
        assert result1.get("duplicate") is not True

        result2 = self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            payload,
            delivery_id="dup-delivery-1",
        )
        assert result2["ok"] is True
        assert result2["duplicate"] is True

    def test_push_no_matching_intent(self, live_server, db_path):
        """Push on a branch with no open intent just passes through."""
        result = self._webhook(
            f"{live_server}/integrations/github/webhook",
            "push",
            {
                "ref": "refs/heads/no-such-branch",
                "after": "some-sha",
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="d-push-nomatch",
        )
        assert result["action"] == "push_processed"
        assert result["revalidated"] == []

    def test_pr_closed_unknown_intent_ignored(self, live_server, db_path):
        """Closing a PR for an unknown intent is handled gracefully."""
        result = self._webhook(
            f"{live_server}/integrations/github/webhook",
            "pull_request",
            {
                "action": "closed",
                "pull_request": {
                    "number": 999,
                    "merged": True,
                    "head": {"ref": "ghost/branch", "sha": "ghost"},
                    "base": {"ref": "main"},
                },
                "repository": {"full_name": "acme/repo"},
            },
            delivery_id="d-close-999",
        )
        assert result["action"] == "ignored"
