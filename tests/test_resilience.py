"""S4 Resilience tests: rate limiting, circuit breakers, timeouts, retry."""

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

from converge.api.rate_limit import TenantRateLimiter, get_limiter, reset_limiter
from converge.resilience import CircuitBreaker, CircuitOpen, OperationTimeout, retry, with_timeout


# ---------------------------------------------------------------------------
# Rate limiter (unit tests)
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_allows_within_limit(self, db_path):
        limiter = TenantRateLimiter(rpm=5, window_seconds=60)
        for _ in range(5):
            assert limiter.is_allowed("tenant-a") is True

    def test_blocks_above_limit(self, db_path):
        limiter = TenantRateLimiter(rpm=3, window_seconds=60)
        for _ in range(3):
            limiter.is_allowed("tenant-a")
        assert limiter.is_allowed("tenant-a") is False

    def test_different_tenants_isolated(self, db_path):
        limiter = TenantRateLimiter(rpm=2, window_seconds=60)
        limiter.is_allowed("a")
        limiter.is_allowed("a")
        assert limiter.is_allowed("a") is False
        # Different tenant still has capacity
        assert limiter.is_allowed("b") is True

    def test_tracks_throttled_metrics(self, db_path):
        limiter = TenantRateLimiter(rpm=1, window_seconds=60)
        limiter.is_allowed("x")
        limiter.is_allowed("x")  # throttled
        assert limiter.total_throttled == 1
        assert limiter.throttled_by_tenant["x"] == 1

    def test_reset_clears_state(self, db_path):
        limiter = TenantRateLimiter(rpm=1, window_seconds=60)
        limiter.is_allowed("x")
        limiter.is_allowed("x")
        limiter.reset()
        assert limiter.total_throttled == 0
        assert limiter.is_allowed("x") is True


# ---------------------------------------------------------------------------
# Rate limiting middleware (integration)
# ---------------------------------------------------------------------------

@pytest.fixture
def live_server(db_path):
    import uvicorn
    from converge.api import create_app

    # Very low rate limit for testing
    with patch.dict(os.environ, {
        "CONVERGE_RATE_LIMIT_RPM": "3",
        "CONVERGE_RATE_LIMIT_ENABLED": "1",
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

        yield f"http://127.0.0.1:{port}"

        server.should_exit = True
        thread.join(timeout=5)
        reset_limiter()


@pytest.mark.integration
class TestRateLimitMiddleware:
    def test_rate_limit_returns_429(self, live_server):
        """Exceeding rate limit returns 429."""
        with patch.dict(os.environ, {"CONVERGE_AUTH_REQUIRED": "0"}):
            # Health is exempt, so use /api/intents
            for _ in range(3):
                urlopen(f"{live_server}/api/intents")

            try:
                urlopen(f"{live_server}/api/intents")
                assert False, "Expected 429"
            except HTTPError as e:
                assert e.code == 429

    def test_health_exempt_from_rate_limit(self, live_server):
        """Health endpoints are not rate limited."""
        for _ in range(10):
            resp = urlopen(f"{live_server}/health")
            assert resp.status == 200


# ---------------------------------------------------------------------------
# Circuit breaker (unit tests)
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_closed_allows_calls(self):
        cb = CircuitBreaker(failure_threshold=3, name="test")
        assert cb.state == CircuitBreaker.CLOSED

        @cb
        def ok():
            return 42

        assert ok() == 42

    def test_opens_after_failures(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10, name="test")

        @cb
        def fail():
            raise ValueError("boom")

        for _ in range(2):
            with pytest.raises(ValueError):
                fail()

        assert cb.state == CircuitBreaker.OPEN
        with pytest.raises(CircuitOpen):
            fail()

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05, name="test")

        @cb
        def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            fail()
        assert cb.state == CircuitBreaker.OPEN

        time.sleep(0.06)
        assert cb.state == CircuitBreaker.HALF_OPEN

    def test_closes_after_success_in_half_open(self):
        cb = CircuitBreaker(
            failure_threshold=1, recovery_timeout=0.01,
            success_threshold=1, name="test",
        )
        cb.record_failure()
        time.sleep(0.02)
        assert cb.state == CircuitBreaker.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED

    def test_reset(self):
        cb = CircuitBreaker(failure_threshold=1, name="test")
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        cb.reset()
        assert cb.state == CircuitBreaker.CLOSED


# ---------------------------------------------------------------------------
# Timeout (unit tests)
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_completes_within_timeout(self):
        @with_timeout(1.0)
        def fast():
            return "done"

        assert fast() == "done"

    def test_raises_on_timeout(self):
        @with_timeout(0.05)
        def slow():
            time.sleep(2)

        with pytest.raises(OperationTimeout):
            slow()

    def test_propagates_exceptions(self):
        @with_timeout(1.0)
        def bad():
            raise RuntimeError("oops")

        with pytest.raises(RuntimeError, match="oops"):
            bad()


# ---------------------------------------------------------------------------
# Retry (unit tests)
# ---------------------------------------------------------------------------

class TestRetry:
    def test_succeeds_first_try(self):
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def ok():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert ok() == "ok"
        assert call_count == 1

    def test_retries_on_failure(self):
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "ok"

        assert flaky() == "ok"
        assert call_count == 3

    def test_raises_after_max_attempts(self):
        @retry(max_attempts=2, base_delay=0.01)
        def always_fail():
            raise RuntimeError("always")

        with pytest.raises(RuntimeError, match="always"):
            always_fail()

    def test_only_retries_specified_exceptions(self):
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01, exceptions=(ValueError,))
        def type_error():
            nonlocal call_count
            call_count += 1
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            type_error()
        assert call_count == 1  # no retry for TypeError
