"""Append-only event log backed by a ConvergeStore singleton.

The event log is the source of truth. Every decision, simulation, check,
policy evaluation, and state change is recorded as an immutable event.
All other state (intents table, projections) is derived from events.

This module is a **facade**: all persistence is delegated to a
``ConvergeStore`` instance (default: ``SqliteStore``).  The store is
initialised once at startup via ``init()`` or ``configure()`` and then
accessed through a global singleton — individual functions no longer
accept a ``db_path`` parameter.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from converge.models import Event, Intent, ReviewTask, RiskLevel, Status, new_id, now_iso  # noqa: F401 — re-export

from converge.ports import ConvergeStore

# ---------------------------------------------------------------------------
# Store singleton (thread-safe)
# ---------------------------------------------------------------------------

_store: ConvergeStore | None = None
_store_lock = threading.Lock()


def configure(store: ConvergeStore) -> None:
    """Set the global store instance (useful for tests and startup).

    Closes the previous store (if any) to avoid leaked connections/pools.
    """
    global _store
    with _store_lock:
        if _store is not None and _store is not store:
            _store.close()
        _store = store


def get_store() -> ConvergeStore | None:
    """Return the current store (may be None if not configured)."""
    return _store


def close() -> None:
    """Close and release the global store instance.

    Safe to call multiple times or when no store is configured.
    """
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
            _store = None


def _get_store() -> ConvergeStore:
    """Return the configured store. Raises if not initialised."""
    if _store is None:
        raise RuntimeError(
            "Store not configured. Call event_log.init() or "
            "event_log.configure() first."
        )
    return _store


# ---------------------------------------------------------------------------
# Trace-ID helper (stays in facade — not a storage concern)
# ---------------------------------------------------------------------------

def _fresh_trace_id() -> str:
    """Generate a fresh trace ID.  Honours CONVERGE_TRACE_ID env var for pinning."""
    return os.environ.get("CONVERGE_TRACE_ID") or f"trace-{new_id()}"

fresh_trace_id = _fresh_trace_id  # public API for engine.py


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def init(db_path: str | Path | None = None, *, backend: str | None = None, dsn: str | None = None) -> None:
    """Initialise (or re-initialise) the store.

    When *backend* is ``None`` the factory reads ``CONVERGE_DB_BACKEND``
    (default ``"sqlite"``).  For backward compatibility, passing only
    *db_path* creates a ``SqliteStore`` directly.
    """
    from converge.adapters.store_factory import create_store
    configure(create_store(backend=backend, db_path=db_path, dsn=dsn))


# ---------------------------------------------------------------------------
# Event operations
# ---------------------------------------------------------------------------

def append(event: Event) -> Event:
    if not event.trace_id:
        event.trace_id = _fresh_trace_id()
    if not event.id:
        event.id = new_id()
    return _get_store().append(event)


def query(
    *,
    event_type: str | None = None,
    intent_id: str | None = None,
    agent_id: str | None = None,
    tenant_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return _get_store().query(
        event_type=event_type, intent_id=intent_id, agent_id=agent_id,
        tenant_id=tenant_id, since=since, until=until, limit=limit,
    )


def count(**filters: Any) -> int:
    return _get_store().count(**filters)


# ---------------------------------------------------------------------------
# Intent materialized view
# ---------------------------------------------------------------------------

def upsert_intent(intent: Intent) -> None:
    _get_store().upsert_intent(intent)


def get_intent(intent_id: str) -> Intent | None:
    return _get_store().get_intent(intent_id)


def list_intents(
    *,
    status: str | None = None,
    tenant_id: str | None = None,
    source: str | None = None,
    limit: int = 200,
) -> list[Intent]:
    return _get_store().list_intents(
        status=status, tenant_id=tenant_id, source=source, limit=limit,
    )


def update_intent_status(intent_id: str, status: Status, retries: int | None = None) -> None:
    _get_store().update_intent_status(intent_id, status, retries=retries)


# ---------------------------------------------------------------------------
# Commit link storage
# ---------------------------------------------------------------------------

def upsert_commit_link(
    intent_id: str, repo: str, sha: str,
    role: str = "head", observed_at: str | None = None,
) -> None:
    _get_store().upsert_commit_link(
        intent_id, repo, sha, role, observed_at or now_iso(),
    )


def list_commit_links(intent_id: str) -> list[dict[str, Any]]:
    return _get_store().list_commit_links(intent_id)


def delete_commit_link(intent_id: str, sha: str, role: str) -> bool:
    return _get_store().delete_commit_link(intent_id, sha, role)


# ---------------------------------------------------------------------------
# Embedding storage
# ---------------------------------------------------------------------------

def upsert_embedding(
    intent_id: str, model: str, dimension: int,
    checksum: str, vector: str, generated_at: str | None = None,
) -> None:
    _get_store().upsert_embedding(
        intent_id, model, dimension, checksum, vector,
        generated_at or now_iso(),
    )


def get_embedding(intent_id: str, model: str) -> dict[str, Any] | None:
    return _get_store().get_embedding(intent_id, model)


def list_embeddings(
    *, tenant_id: str | None = None,
    model: str | None = None, limit: int = 1000,
) -> list[dict[str, Any]]:
    return _get_store().list_embeddings(
        tenant_id=tenant_id, model=model, limit=limit,
    )


def delete_embedding(intent_id: str, model: str) -> bool:
    return _get_store().delete_embedding(intent_id, model)


def embedding_coverage(
    *, tenant_id: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    return _get_store().embedding_coverage(
        tenant_id=tenant_id, model=model,
    )


# ---------------------------------------------------------------------------
# Review task storage
# ---------------------------------------------------------------------------

def upsert_review_task(task: ReviewTask) -> None:
    _get_store().upsert_review_task(task)


def get_review_task(task_id: str) -> ReviewTask | None:
    return _get_store().get_review_task(task_id)


def list_review_tasks(
    *,
    intent_id: str | None = None,
    status: str | None = None,
    reviewer: str | None = None,
    tenant_id: str | None = None,
    limit: int = 200,
) -> list[ReviewTask]:
    return _get_store().list_review_tasks(
        intent_id=intent_id, status=status, reviewer=reviewer,
        tenant_id=tenant_id, limit=limit,
    )


def update_review_task_status(task_id: str, status: str, **fields: Any) -> None:
    _get_store().update_review_task_status(task_id, status, **fields)


# ---------------------------------------------------------------------------
# Intake override storage
# ---------------------------------------------------------------------------

def upsert_intake_override(
    *, tenant_id: str, mode: str,
    set_by: str = "system", reason: str = "",
) -> None:
    _get_store().upsert_intake_override(tenant_id, mode, set_by, reason)


def get_intake_override(*, tenant_id: str) -> dict[str, Any] | None:
    return _get_store().get_intake_override(tenant_id)


def delete_intake_override(*, tenant_id: str) -> bool:
    return _get_store().delete_intake_override(tenant_id)


# ---------------------------------------------------------------------------
# Security findings storage
# ---------------------------------------------------------------------------

def upsert_security_finding(finding: dict[str, Any]) -> None:
    _get_store().upsert_security_finding(finding)


def list_security_findings(
    *,
    intent_id: str | None = None,
    scanner: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    tenant_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return _get_store().list_security_findings(
        intent_id=intent_id, scanner=scanner, severity=severity,
        category=category, tenant_id=tenant_id, limit=limit,
    )


def count_security_findings(
    *,
    intent_id: str | None = None,
    severity: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, int]:
    return _get_store().count_security_findings(
        intent_id=intent_id, severity=severity, tenant_id=tenant_id,
    )


# ---------------------------------------------------------------------------
# Agent policy storage
# ---------------------------------------------------------------------------

def upsert_agent_policy(data: dict[str, Any]) -> None:
    _get_store().upsert_agent_policy(data)


def get_agent_policy(agent_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    return _get_store().get_agent_policy(agent_id, tenant_id=tenant_id)


def list_agent_policies(tenant_id: str | None = None) -> list[dict[str, Any]]:
    return _get_store().list_agent_policies(tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Risk policy storage
# ---------------------------------------------------------------------------

def upsert_risk_policy(tenant_id: str, data: dict[str, Any]) -> None:
    _get_store().upsert_risk_policy(tenant_id, data)


def get_risk_policy(tenant_id: str) -> dict[str, Any] | None:
    return _get_store().get_risk_policy(tenant_id)


def list_risk_policies(tenant_id: str | None = None) -> list[dict[str, Any]]:
    return _get_store().list_risk_policies(tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Compliance thresholds storage
# ---------------------------------------------------------------------------

def upsert_compliance_thresholds(tenant_id: str, data: dict[str, Any]) -> None:
    _get_store().upsert_compliance_thresholds(tenant_id, data)


def get_compliance_thresholds(tenant_id: str) -> dict[str, Any] | None:
    return _get_store().get_compliance_thresholds(tenant_id)


def list_compliance_thresholds(tenant_id: str | None = None) -> list[dict[str, Any]]:
    return _get_store().list_compliance_thresholds(tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Queue lock (advisory locking)
# ---------------------------------------------------------------------------

def acquire_queue_lock(
    lock_name: str = "queue",
    holder_pid: int | None = None,
    ttl_seconds: int = 300,
) -> bool:
    return _get_store().acquire_queue_lock(lock_name=lock_name, holder_pid=holder_pid, ttl_seconds=ttl_seconds)


def release_queue_lock(
    lock_name: str = "queue",
    holder_pid: int | None = None,
) -> bool:
    return _get_store().release_queue_lock(lock_name=lock_name, holder_pid=holder_pid)


def force_release_queue_lock(lock_name: str = "queue") -> bool:
    return _get_store().force_release_queue_lock(lock_name=lock_name)


def get_queue_lock_info(lock_name: str = "queue") -> dict[str, Any] | None:
    return _get_store().get_queue_lock_info(lock_name=lock_name)


# ---------------------------------------------------------------------------
# Webhook delivery dedup
# ---------------------------------------------------------------------------

def is_duplicate_delivery(delivery_id: str) -> bool:
    return _get_store().is_duplicate_delivery(delivery_id)


def record_delivery(delivery_id: str) -> None:
    _get_store().record_delivery(delivery_id)


# ---------------------------------------------------------------------------
# Event chain state
# ---------------------------------------------------------------------------

def get_chain_state(chain_id: str = "main") -> dict[str, Any] | None:
    return _get_store().get_chain_state(chain_id)


def save_chain_state(chain_id: str, last_hash: str, event_count: int) -> None:
    _get_store().save_chain_state(chain_id, last_hash, event_count)


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

def prune_events(before: str, tenant_id: str | None = None, dry_run: bool = False) -> int:
    return _get_store().prune_events(before, tenant_id=tenant_id, dry_run=dry_run)
