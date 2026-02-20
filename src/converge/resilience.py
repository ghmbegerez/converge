"""Resilience primitives: circuit breaker, timeout, retry with backoff.

No external dependencies.  Designed for wrapping I/O calls to git,
external checks, and database operations.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from typing import Any, Callable, TypeVar

log = logging.getLogger("converge.resilience")

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitOpen(Exception):
    """Raised when the circuit breaker is in OPEN state."""
    pass


class CircuitBreaker:
    """Three-state circuit breaker (CLOSED → OPEN → HALF_OPEN → CLOSED).

    Parameters
    ----------
    failure_threshold:
        Number of consecutive failures before opening the circuit.
    recovery_timeout:
        Seconds to wait in OPEN state before switching to HALF_OPEN.
    success_threshold:
        Number of consecutive successes in HALF_OPEN to close the circuit.
    name:
        Human-readable name for logging.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        name: str = "default",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.name = name

        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN:
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    self._state = self.HALF_OPEN
                    self._success_count = 0
            return self._state

    def record_success(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = self.CLOSED
                    self._failure_count = 0
                    log.info("Circuit breaker '%s' closed", self.name)
            else:
                self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                log.warning("Circuit breaker '%s' re-opened from half_open", self.name)
            elif self._failure_count >= self.failure_threshold:
                self._state = self.OPEN
                log.warning("Circuit breaker '%s' opened after %d failures", self.name, self._failure_count)

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Use as a decorator."""

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            if self.state == self.OPEN:
                raise CircuitOpen(f"Circuit breaker '{self.name}' is open")
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception:
                self.record_failure()
                raise

        return wrapper

    def reset(self) -> None:
        """Reset to closed state (for tests)."""
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._success_count = 0


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

class OperationTimeout(Exception):
    """Raised when an operation exceeds its configured timeout."""
    pass


def with_timeout(seconds: float) -> Callable:
    """Decorator that raises ``OperationTimeout`` if the wrapped function
    takes longer than *seconds*.

    Uses a daemon thread so we don't block the caller forever.
    Note: this only interrupts at the Python level; it cannot interrupt
    blocking C-level calls.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            result: list[Any] = []
            exception: list[BaseException] = []

            def target() -> None:
                try:
                    result.append(func(*args, **kwargs))
                except BaseException as e:
                    exception.append(e)

            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            thread.join(timeout=seconds)
            if thread.is_alive():
                raise OperationTimeout(
                    f"{func.__name__} exceeded timeout of {seconds}s"
                )
            if exception:
                raise exception[0]
            return result[0]

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Retry with exponential backoff
# ---------------------------------------------------------------------------

def retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable:
    """Decorator: retry with bounded exponential backoff.

    Parameters
    ----------
    max_attempts:
        Total number of attempts (including the first).
    base_delay:
        Initial delay in seconds between retries.
    max_delay:
        Maximum delay cap.
    backoff_factor:
        Multiplier applied to delay after each failure.
    exceptions:
        Tuple of exception classes that trigger a retry.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = base_delay
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        break
                    log.warning(
                        "Retry %d/%d for %s: %s (delay %.1fs)",
                        attempt, max_attempts, func.__name__, e, delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)
            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator
