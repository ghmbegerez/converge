"""S8: Multi-tenant load tests — P95/P99 latency, throughput, error rate."""

from __future__ import annotations

import json
import os
import socket
import statistics
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
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def load_server(db_path):
    """Uvicorn server with auth/rate-limit disabled for pure load measurement."""
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


def _get(url: str) -> tuple[int, float]:
    """GET request returning (status_code, latency_ms)."""
    t0 = time.perf_counter()
    try:
        resp = urlopen(url, timeout=10)
        status = resp.status
    except HTTPError as e:
        status = e.code
    except (URLError, OSError):
        status = 0
    latency = (time.perf_counter() - t0) * 1000
    return status, latency


def _post_json(url: str, data: dict, headers: dict | None = None) -> tuple[int, float]:
    """POST JSON returning (status_code, latency_ms)."""
    t0 = time.perf_counter()
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = Request(url, data=json.dumps(data).encode(), headers=hdrs, method="POST")
    try:
        resp = urlopen(req, timeout=10)
        status = resp.status
    except HTTPError as e:
        status = e.code
    except (URLError, OSError):
        status = 0
    latency = (time.perf_counter() - t0) * 1000
    return status, latency


# ---------------------------------------------------------------------------
# Load tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLoadMultiTenant:

    def test_concurrent_health_throughput(self, load_server):
        """50 concurrent health requests complete with P99 < 500ms and 0% errors."""
        n_requests = 50
        n_workers = 10
        url = f"{load_server}/health"

        latencies: list[float] = []
        errors = 0

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_get, url) for _ in range(n_requests)]
            for f in as_completed(futures):
                status, latency = f.result()
                latencies.append(latency)
                if status != 200:
                    errors += 1

        p95 = sorted(latencies)[int(n_requests * 0.95)]
        p99 = sorted(latencies)[int(n_requests * 0.99)]

        assert errors == 0, f"Health requests had {errors} errors"
        assert p99 < 500, f"P99 latency {p99:.1f}ms exceeds 500ms"
        assert len(latencies) == n_requests

    def test_concurrent_intent_listing_multi_tenant(self, load_server, db_path):
        """3 tenants with 10 intents each, 30 concurrent list requests."""
        tenants = ["tenant-a", "tenant-b", "tenant-c"]
        for t in tenants:
            for i in range(10):
                intent = Intent(
                    id=f"{t}:intent-{i}",
                    source=f"feature/{t}-{i}",
                    target="main",
                    status=Status.READY,
                    created_by="load-test",
                    tenant_id=t,
                    technical={"initial_base_commit": f"sha-{i}"},
                )
                event_log.upsert_intent(db_path, intent)

        n_requests = 30
        n_workers = 10
        url = f"{load_server}/api/intents"

        latencies: list[float] = []
        errors = 0

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(_get, url) for _ in range(n_requests)]
            for f in as_completed(futures):
                status, latency = f.result()
                latencies.append(latency)
                if status != 200:
                    errors += 1

        p95 = sorted(latencies)[int(n_requests * 0.95)]

        assert errors == 0, f"Intent list requests had {errors} errors"
        assert p95 < 1000, f"P95 latency {p95:.1f}ms exceeds 1000ms"

    def test_sustained_load_error_rate(self, load_server, db_path):
        """100 mixed requests across endpoints — error rate < 5%."""
        # Seed data
        for i in range(5):
            intent = Intent(
                id=f"load-{i}",
                source=f"feature/load-{i}",
                target="main",
                status=Status.READY,
                created_by="load-test",
                technical={"initial_base_commit": f"sha-{i}"},
            )
            event_log.upsert_intent(db_path, intent)

        endpoints = [
            f"{load_server}/health",
            f"{load_server}/health/ready",
            f"{load_server}/health/live",
            f"{load_server}/api/intents",
            f"{load_server}/metrics",
        ]

        n_requests = 100
        errors = 0
        latencies: list[float] = []

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [
                pool.submit(_get, endpoints[i % len(endpoints)])
                for i in range(n_requests)
            ]
            for f in as_completed(futures):
                status, latency = f.result()
                latencies.append(latency)
                if status not in (200,):
                    errors += 1

        error_rate = errors / n_requests
        avg_latency = statistics.mean(latencies)
        p99 = sorted(latencies)[int(n_requests * 0.99)]

        assert error_rate < 0.05, f"Error rate {error_rate:.1%} exceeds 5%"
        assert p99 < 2000, f"P99 {p99:.1f}ms exceeds 2000ms"

    def test_concurrent_webhook_throughput(self, load_server):
        """30 concurrent webhook POSTs — all succeed, throughput > 5 req/s."""
        n_requests = 30
        n_workers = 10
        url = f"{load_server}/integrations/github/webhook"

        latencies: list[float] = []
        errors = 0

        def send_webhook(i: int) -> tuple[int, float]:
            return _post_json(
                url,
                {"zen": f"test-{i}"},
                headers={
                    "X-GitHub-Event": "ping",
                    "X-GitHub-Delivery": f"load-delivery-{i}",
                },
            )

        t_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(send_webhook, i) for i in range(n_requests)]
            for f in as_completed(futures):
                status, latency = f.result()
                latencies.append(latency)
                if status != 200:
                    errors += 1
        elapsed = time.perf_counter() - t_start

        throughput = n_requests / elapsed
        assert errors == 0, f"Webhook load had {errors} errors"
        assert throughput > 5, f"Throughput {throughput:.1f} req/s below 5 req/s"

    def test_rate_limiter_isolates_tenants(self, db_path):
        """Tenant A hitting rate limit does NOT throttle Tenant B."""
        import uvicorn
        from converge.api import create_app
        from converge.api.rate_limit import reset_limiter

        with patch.dict(os.environ, {
            "CONVERGE_AUTH_REQUIRED": "0",
            "CONVERGE_RATE_LIMIT_ENABLED": "1",
            "CONVERGE_RATE_LIMIT_RPM": "5",
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

                # Exhaust Tenant A's limit (5 RPM)
                for i in range(8):
                    req = Request(f"{base}/api/intents")
                    req.add_header("x-tenant-id", "tenant-A")
                    try:
                        urlopen(req, timeout=5)
                    except HTTPError:
                        pass

                # Tenant B should still be allowed
                req = Request(f"{base}/api/intents")
                req.add_header("x-tenant-id", "tenant-B")
                resp = urlopen(req, timeout=5)
                assert resp.status == 200, "Tenant B should not be rate-limited"
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                reset_limiter()
