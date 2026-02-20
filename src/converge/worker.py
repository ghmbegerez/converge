"""Autonomous queue worker for Converge.

Runs as a **separate process** (no shared event loop with the API server).
Polls for VALIDATED intents, processes them, and optionally publishes
results to GitHub.

Usage:
    python -m converge.worker            # uses env vars
    converge worker                      # via CLI

Configuration (env vars):
    CONVERGE_WORKER_POLL_INTERVAL   — seconds between polls (default 5)
    CONVERGE_WORKER_BATCH_SIZE      — max intents per batch (default 20)
    CONVERGE_WORKER_MAX_RETRIES     — per-intent retry limit (default 3)
    CONVERGE_WORKER_TARGET          — target branch (default "main")
    CONVERGE_WORKER_AUTO_CONFIRM    — "1" to auto-merge (default "0")

GitHub integration (optional):
    CONVERGE_GITHUB_APP_ID          — enables GitHub publishing
    CONVERGE_GITHUB_INSTALLATION_ID — required if app_id is set
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from converge import engine, event_log
from converge.models import Event, EventType

log = logging.getLogger("converge.worker")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class WorkerConfig:
    """Worker runtime configuration from environment."""

    def __init__(self) -> None:
        self.poll_interval = int(os.environ.get("CONVERGE_WORKER_POLL_INTERVAL", "5"))
        self.batch_size = int(os.environ.get("CONVERGE_WORKER_BATCH_SIZE", "20"))
        self.max_retries = int(os.environ.get("CONVERGE_WORKER_MAX_RETRIES", "3"))
        self.target = os.environ.get("CONVERGE_WORKER_TARGET", "main")
        self.auto_confirm = os.environ.get("CONVERGE_WORKER_AUTO_CONFIRM", "0") == "1"
        self.db_path = os.environ.get("CONVERGE_DB_PATH", str(Path(".converge") / "state.db"))
        # GitHub publishing (optional)
        self.github_app_id = os.environ.get("CONVERGE_GITHUB_APP_ID", "")
        self.github_installation_id = os.environ.get("CONVERGE_GITHUB_INSTALLATION_ID", "")

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_app_id and self.github_installation_id)


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

class QueueWorker:
    """Polling-based queue processor with graceful shutdown."""

    def __init__(self, config: WorkerConfig | None = None) -> None:
        self.config = config or WorkerConfig()
        self._running = False
        self._draining = False
        self._cycles = 0
        self._total_processed = 0

    def start(self) -> None:
        """Start the worker loop (blocking). Installs signal handlers."""
        self._running = True
        self._install_signal_handlers()

        log.info(
            "Worker starting — poll=%ds batch=%d target=%s auto_confirm=%s github=%s",
            self.config.poll_interval,
            self.config.batch_size,
            self.config.target,
            self.config.auto_confirm,
            self.config.github_enabled,
        )

        # Initialise event store
        event_log.init(self.config.db_path)

        event_log.append(self.config.db_path, Event(
            event_type=EventType.WORKER_STARTED,
            payload={
                "poll_interval": self.config.poll_interval,
                "batch_size": self.config.batch_size,
                "pid": os.getpid(),
            },
        ))

        try:
            while self._running:
                self._poll_once()
                if not self._running:
                    break
                time.sleep(self.config.poll_interval)
        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal the worker to stop after the current batch."""
        log.info("Worker stop requested — draining current batch")
        self._draining = True
        self._running = False

    def _install_signal_handlers(self) -> None:
        """Capture SIGTERM and SIGINT for graceful shutdown.

        Only works from the main thread; silently skips otherwise
        (e.g. when run inside a test thread).
        """
        import threading
        if threading.current_thread() is not threading.main_thread():
            log.debug("Not main thread — skipping signal handler installation")
            return

        def _handler(signum: int, frame: Any) -> None:
            sig_name = signal.Signals(signum).name
            log.info("Received %s — initiating graceful shutdown", sig_name)
            self.stop()

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def _poll_once(self) -> list[dict[str, Any]]:
        """Execute one processing cycle."""
        self._cycles += 1
        try:
            results = engine.process_queue(
                self.config.db_path,
                limit=self.config.batch_size,
                target=self.config.target,
                auto_confirm=self.config.auto_confirm,
                max_retries=self.config.max_retries,
                skip_checks=True,           # worker revalidates but skips heavy checks
                use_last_simulation=True,    # reuse existing simulations
            )
        except Exception:
            log.exception("Error during queue processing (cycle %d)", self._cycles)
            return []

        if results and not (len(results) == 1 and "error" in results[0]):
            self._total_processed += len(results)
            log.info("Cycle %d: processed %d intents", self._cycles, len(results))

            if self.config.github_enabled:
                self._publish_results(results)

        return results

    def _publish_results(self, results: list[dict[str, Any]]) -> None:
        """Publish decisions to GitHub (runs async in a one-shot event loop)."""
        try:
            asyncio.run(self._async_publish(results))
        except Exception:
            log.exception("Failed to publish results to GitHub")

    async def _async_publish(self, results: list[dict[str, Any]]) -> None:
        """Async batch publish of decisions to GitHub."""
        from converge.integrations.github_app import publish_decision

        installation_id = int(self.config.github_installation_id)

        async with httpx.AsyncClient() as client:
            for result in results:
                intent_id = result.get("intent_id", "")
                decision = result.get("decision", "")
                if not intent_id or not decision:
                    continue

                # Extract repo info from intent
                intent = event_log.get_intent(self.config.db_path, intent_id)
                if not intent:
                    continue
                repo_full = intent.technical.get("repo", "")
                head_sha = intent.technical.get("initial_base_commit", "")
                if not repo_full or not head_sha:
                    continue

                parts = repo_full.split("/", 1)
                if len(parts) != 2:
                    continue
                owner, repo = parts

                await publish_decision(
                    owner=owner,
                    repo=repo,
                    installation_id=installation_id,
                    head_sha=head_sha,
                    intent_id=intent_id,
                    decision=decision,
                    trace_id=result.get("trace_id", ""),
                    risk_score=result.get("risk", {}).get("risk_score", 0.0),
                    reason=result.get("reason", ""),
                    client=client,
                )

    def _shutdown(self) -> None:
        """Clean shutdown: release lock, log final state."""
        log.info(
            "Worker shutting down — cycles=%d total_processed=%d",
            self._cycles,
            self._total_processed,
        )
        # Force-release lock in case we hold it
        try:
            event_log.force_release_queue_lock(self.config.db_path)
        except Exception:
            pass

        event_log.append(self.config.db_path, Event(
            event_type=EventType.WORKER_STOPPED,
            payload={
                "cycles": self._cycles,
                "total_processed": self._total_processed,
                "pid": os.getpid(),
            },
        ))

    # Public read-only state for tests / monitoring
    @property
    def cycles(self) -> int:
        return self._cycles

    @property
    def total_processed(self) -> int:
        return self._total_processed

    @property
    def is_running(self) -> bool:
        return self._running


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_worker(db_path: str | None = None) -> None:
    """Start the worker (blocking). For CLI / __main__."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    config = WorkerConfig()
    if db_path:
        config.db_path = db_path
    worker = QueueWorker(config)
    worker.start()


if __name__ == "__main__":
    run_worker()
