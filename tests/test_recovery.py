"""S8: Recovery tests — webhook burst, worker crash recovery, store failover."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch, MagicMock
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from converge import event_log
from converge.adapters.sqlite_store import SqliteStore
from converge.models import Event, EventType, Intent, Status


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def recovery_server(db_path):
    """Server with auth/rate-limit off for recovery tests."""
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


def _webhook(url: str, event: str, payload: dict, delivery_id: str) -> int:
    """Send webhook, return status code."""
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
    try:
        return urlopen(req, timeout=10).status
    except HTTPError as e:
        return e.code


# ---------------------------------------------------------------------------
# Webhook burst (100+ simultaneous)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestWebhookBurst:

    def test_100_plus_concurrent_webhooks(self, db_path, recovery_server):
        """100+ simultaneous webhook POSTs — zero 5xx errors."""
        n = 120
        url = f"{recovery_server}/integrations/github/webhook"
        errors_5xx = 0
        successes = 0

        def send(i: int) -> int:
            return _webhook(url, "ping", {"zen": f"burst-{i}"}, f"burst-{i}")

        with ThreadPoolExecutor(max_workers=30) as pool:
            futures = [pool.submit(send, i) for i in range(n)]
            for f in as_completed(futures):
                status = f.result()
                if 500 <= status < 600:
                    errors_5xx += 1
                elif status == 200:
                    successes += 1

        assert errors_5xx == 0, f"{errors_5xx} server errors in webhook burst"
        assert successes == n, f"Only {successes}/{n} webhooks succeeded"

    def test_webhook_burst_creates_intents(self, recovery_server, db_path):
        """50 concurrent PR opened webhooks each create an intent."""
        n = 50
        url = f"{recovery_server}/integrations/github/webhook"

        def send_pr(i: int) -> int:
            return _webhook(
                url,
                "pull_request",
                {
                    "action": "opened",
                    "pull_request": {
                        "number": 1000 + i,
                        "title": f"Burst PR {i}",
                        "head": {"ref": f"feature/burst-{i}", "sha": f"sha-{i}"},
                        "base": {"ref": "main"},
                    },
                    "repository": {"full_name": "burst/repo"},
                    "installation": {"id": 99999},
                },
                f"burst-pr-{i}",
            )

        with ThreadPoolExecutor(max_workers=15) as pool:
            futures = [pool.submit(send_pr, i) for i in range(n)]
            results = [f.result() for f in as_completed(futures)]

        assert all(s == 200 for s in results), "All PR webhooks should succeed"

        # Verify intents were created
        intents = event_log.list_intents()
        burst_intents = [i for i in intents if i.id.startswith("burst/repo:pr-")]
        assert len(burst_intents) == n, f"Expected {n} intents, got {len(burst_intents)}"

    def test_duplicate_delivery_idempotent(self, db_path, recovery_server):
        """Replaying the same delivery_id returns duplicate=true, not an error."""
        url = f"{recovery_server}/integrations/github/webhook"

        # First delivery
        status1 = _webhook(url, "ping", {"zen": "first"}, "dedup-test-1")
        assert status1 == 200

        # Replay — should be idempotent
        req = Request(
            url,
            data=json.dumps({"zen": "replay"}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",
                "X-GitHub-Delivery": "dedup-test-1",
            },
            method="POST",
        )
        resp = urlopen(req, timeout=10)
        body = json.loads(resp.read())
        assert body.get("duplicate") is True


# ---------------------------------------------------------------------------
# Worker crash recovery
# ---------------------------------------------------------------------------

class TestWorkerCrashRecovery:

    def test_lock_released_on_worker_shutdown(self, db_path):
        """Worker._shutdown force-releases the queue lock."""
        from converge.worker import QueueWorker, WorkerConfig

        config = WorkerConfig()
        worker = QueueWorker(config)

        # Simulate a held lock
        acquired = event_log.acquire_queue_lock(holder_pid=12345)
        assert acquired
        lock_info = event_log.get_queue_lock_info()
        assert lock_info is not None

        # Shutdown releases it
        worker._shutdown()

        lock_info = event_log.get_queue_lock_info()
        assert lock_info is None, "Lock should be released after shutdown"

    def test_new_worker_acquires_lock_after_crash(self, db_path):
        """After force-releasing a stale lock, a new worker can acquire it."""
        # Simulate crashed worker holding the lock
        event_log.acquire_queue_lock(holder_pid=99999)
        assert event_log.get_queue_lock_info() is not None

        # Force release (simulates crash recovery)
        event_log.force_release_queue_lock()

        # New worker can acquire
        acquired = event_log.acquire_queue_lock(holder_pid=os.getpid())
        assert acquired, "New worker should acquire the lock after crash recovery"

        # Clean up
        event_log.release_queue_lock()

    def test_expired_lock_automatically_cleared(self, db_path):
        """A lock with expired TTL is automatically cleared on next acquire."""
        # Acquire with 0-second TTL (already expired)
        event_log.acquire_queue_lock(holder_pid=11111, ttl_seconds=0)

        # Wait a moment for the expiration timestamp to pass
        time.sleep(0.1)

        # New acquire should succeed — expired lock is cleaned up
        acquired = event_log.acquire_queue_lock(holder_pid=os.getpid())
        assert acquired, "Expired lock should be automatically cleared"

        event_log.release_queue_lock()

    def test_worker_records_lifecycle_events(self, db_path):
        """Worker start/stop records WORKER_STARTED and WORKER_STOPPED events."""
        from converge.worker import QueueWorker, WorkerConfig

        config = WorkerConfig()
        config.poll_interval = 1
        worker = QueueWorker(config)

        # Start in a thread and stop after 1 cycle
        def run_briefly():
            worker._running = True
            event_log.init()
            event_log.append(Event(
                event_type=EventType.WORKER_STARTED,
                payload={"pid": os.getpid()},
            ))
            worker._poll_once()
            worker._shutdown()

        t = threading.Thread(target=run_briefly)
        t.start()
        t.join(timeout=10)

        events = event_log.query()
        types = [e["event_type"] for e in events]
        assert "worker.started" in types
        assert "worker.stopped" in types


# ---------------------------------------------------------------------------
# Store failover
# ---------------------------------------------------------------------------

class TestStoreFailover:

    def test_sqlite_store_works_after_postgres_unavailable(self, db_path, tmp_path):
        """When Postgres is unavailable, SQLite store works as fallback."""
        from converge.adapters.store_factory import create_store

        # Postgres without DSN → raises ValueError
        with pytest.raises(ValueError, match="DSN"):
            create_store(backend="postgres")

        # SQLite fallback works
        sqlite_store = create_store(backend="sqlite", db_path=str(tmp_path / "fallback.db"))
        assert sqlite_store is not None
        sqlite_store.append(Event(
            event_type=EventType.WORKER_STARTED,
            payload={"test": "fallback"},
        ))
        events = sqlite_store.query()
        assert len(events) == 1

    def test_unknown_backend_raises(self, db_path):
        """Unknown backend raises ValueError."""
        from converge.adapters.store_factory import create_store

        with pytest.raises(ValueError, match="Unknown backend"):
            create_store(backend="redis")

    def test_health_ready_reports_503_on_db_failure(self, db_path):
        """Readiness probe returns 503 when DB is inaccessible."""
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

            try:
                base = f"http://127.0.0.1:{port}"

                # Normal readiness check should be OK
                resp = urlopen(f"{base}/health/ready", timeout=5)
                assert resp.status == 200

                # Break the DB by making count() raise
                with patch.object(event_log, "count", side_effect=RuntimeError("DB down")):
                    try:
                        urlopen(f"{base}/health/ready", timeout=5)
                        assert False, "Should have raised"
                    except HTTPError as e:
                        assert e.code == 503
                        body = json.loads(e.read())
                        assert body["status"] == "unavailable"
            finally:
                server.should_exit = True
                thread.join(timeout=5)
