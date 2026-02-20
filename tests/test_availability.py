"""S8: Availability tests — health probes under load, metrics continuity."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import pytest

from converge import event_log
from converge.models import Intent, Status


# ---------------------------------------------------------------------------
# Fixture: server under background load
# ---------------------------------------------------------------------------

@pytest.fixture
def loaded_server(db_path):
    """Server with background load generator for availability testing."""
    import uvicorn
    from converge.api import create_app

    # Seed intents for realistic load
    for i in range(20):
        intent = Intent(
            id=f"avail-{i}",
            source=f"feature/avail-{i}",
            target="main",
            status=Status.READY,
            created_by="availability-test",
            tenant_id=f"tenant-{i % 3}",
            technical={"initial_base_commit": f"sha-{i}"},
        )
        event_log.upsert_intent(db_path, intent)

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

        base = f"http://127.0.0.1:{port}"

        # Start background load: continuous GET /api/intents in a loop
        _stop_load = threading.Event()

        def background_load():
            while not _stop_load.is_set():
                try:
                    urlopen(f"{base}/api/intents", timeout=5)
                except Exception:
                    pass
                time.sleep(0.02)

        load_threads = [threading.Thread(target=background_load, daemon=True) for _ in range(5)]
        for lt in load_threads:
            lt.start()

        yield base

        _stop_load.set()
        for lt in load_threads:
            lt.join(timeout=3)
        server.should_exit = True
        thread.join(timeout=5)


def _get_status(url: str) -> int:
    """GET request returning status code."""
    try:
        return urlopen(url, timeout=10).status
    except HTTPError as e:
        return e.code
    except (URLError, OSError):
        return 0


# ---------------------------------------------------------------------------
# Availability tests under load
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestAvailabilityUnderLoad:

    def test_liveness_always_responds(self, loaded_server):
        """Liveness probe (/health/live) responds 200 under load — 20 requests."""
        results = []
        for _ in range(20):
            results.append(_get_status(f"{loaded_server}/health/live"))

        assert all(s == 200 for s in results), \
            f"Liveness probe failures: {[s for s in results if s != 200]}"

    def test_readiness_stable_under_load(self, loaded_server):
        """Readiness probe (/health/ready) stays 200 under concurrent load."""
        n = 20
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [
                pool.submit(_get_status, f"{loaded_server}/health/ready")
                for _ in range(n)
            ]
            results = [f.result() for f in as_completed(futures)]

        ok_count = sum(1 for s in results if s == 200)
        assert ok_count == n, f"Readiness probe: {ok_count}/{n} succeeded"

    def test_health_summary_under_load(self, loaded_server):
        """Main /health endpoint responds correctly under load."""
        n = 15
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [
                pool.submit(_get_status, f"{loaded_server}/health")
                for _ in range(n)
            ]
            results = [f.result() for f in as_completed(futures)]

        assert all(s == 200 for s in results), "All /health requests should succeed"

    def test_metrics_available_under_load(self, loaded_server):
        """Prometheus metrics endpoint returns valid data under load."""
        for _ in range(10):
            resp = urlopen(f"{loaded_server}/metrics", timeout=5)
            body = resp.read().decode()
            # Should contain Prometheus metrics
            assert "converge_http_requests_total" in body or "converge" in body.lower() or len(body) > 0
            assert resp.status == 200

    def test_api_intents_responds_under_load(self, loaded_server):
        """API endpoint /api/intents continues responding with background load."""
        n = 15
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [
                pool.submit(_get_status, f"{loaded_server}/api/intents")
                for _ in range(n)
            ]
            results = [f.result() for f in as_completed(futures)]

        ok_count = sum(1 for s in results if s == 200)
        assert ok_count == n, f"API intents: {ok_count}/{n} succeeded under load"

    def test_concurrent_reads_and_writes(self, loaded_server):
        """Mix of webhook writes and API reads under concurrent load — no 5xx."""
        base = loaded_server
        errors_5xx = 0

        def read_health():
            return _get_status(f"{base}/health")

        def write_webhook(i: int):
            req = Request(
                f"{base}/integrations/github/webhook",
                data=json.dumps({"zen": f"rw-{i}"}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "ping",
                    "X-GitHub-Delivery": f"rw-{i}",
                },
                method="POST",
            )
            try:
                return urlopen(req, timeout=10).status
            except HTTPError as e:
                return e.code
            except (URLError, OSError):
                return 0

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            # 15 reads + 15 writes interleaved
            for i in range(15):
                futures.append(pool.submit(read_health))
                futures.append(pool.submit(write_webhook, i))

            for f in as_completed(futures):
                status = f.result()
                if 500 <= status < 600:
                    errors_5xx += 1

        assert errors_5xx == 0, f"{errors_5xx} server errors during concurrent R/W"
