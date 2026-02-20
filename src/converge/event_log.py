"""Append-only event log backed by SQLite.

The event log is the source of truth. Every decision, simulation, check,
policy evaluation, and state change is recorded as an immutable event.
All other state (intents table, projections) is derived from events.

This module is now a **facade**: all persistence is delegated to a
``ConvergeStore`` instance (default: ``SqliteStore``).  The public API
(function signatures, return types) is unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from converge.models import Event, Intent, RiskLevel, Status, new_id, now_iso  # noqa: F401 — re-export

from converge.ports import ConvergeStore

# ---------------------------------------------------------------------------
# Store singleton
# ---------------------------------------------------------------------------

_store: ConvergeStore | None = None


def configure(store: ConvergeStore) -> None:
    """Set the global store instance (useful for tests and startup)."""
    global _store
    _store = store


def get_store() -> ConvergeStore | None:
    """Return the current store (may be None if not configured)."""
    return _store


def _ensure_store(db_path: str | Path | None = None) -> ConvergeStore:
    """Return the current store, auto-initialising from *db_path* if needed."""
    global _store
    if _store is not None:
        return _store
    if db_path is None:
        raise RuntimeError("No store configured and no db_path provided")
    from converge.adapters.sqlite_store import SqliteStore
    _store = SqliteStore(db_path)
    return _store


# ---------------------------------------------------------------------------
# Trace-ID helper (stays in facade — not a storage concern)
# ---------------------------------------------------------------------------

def _fresh_trace_id() -> str:
    """Generate a fresh trace ID.  Honours CONVERGE_TRACE_ID env var for pinning."""
    return os.environ.get("CONVERGE_TRACE_ID") or f"trace-{new_id()}"


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

def append(db_path: str | Path, event: Event) -> Event:
    if not event.trace_id:
        event.trace_id = _fresh_trace_id()
    if not event.id:
        event.id = new_id()
    return _ensure_store(db_path).append(event)


def query(
    db_path: str | Path,
    *,
    event_type: str | None = None,
    intent_id: str | None = None,
    agent_id: str | None = None,
    tenant_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return _ensure_store(db_path).query(
        event_type=event_type, intent_id=intent_id, agent_id=agent_id,
        tenant_id=tenant_id, since=since, until=until, limit=limit,
    )


def count(db_path: str | Path, **filters: Any) -> int:
    return _ensure_store(db_path).count(**filters)


# ---------------------------------------------------------------------------
# Intent materialized view
# ---------------------------------------------------------------------------

def upsert_intent(db_path: str | Path, intent: Intent) -> None:
    _ensure_store(db_path).upsert_intent(intent)


def get_intent(db_path: str | Path, intent_id: str) -> Intent | None:
    return _ensure_store(db_path).get_intent(intent_id)


def list_intents(
    db_path: str | Path,
    *,
    status: str | None = None,
    tenant_id: str | None = None,
    limit: int = 200,
) -> list[Intent]:
    return _ensure_store(db_path).list_intents(status=status, tenant_id=tenant_id, limit=limit)


def update_intent_status(db_path: str | Path, intent_id: str, status: Status, retries: int | None = None) -> None:
    _ensure_store(db_path).update_intent_status(intent_id, status, retries=retries)


# ---------------------------------------------------------------------------
# Agent policy storage
# ---------------------------------------------------------------------------

def upsert_agent_policy(db_path: str | Path, data: dict[str, Any]) -> None:
    _ensure_store(db_path).upsert_agent_policy(data)


def get_agent_policy(db_path: str | Path, agent_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    return _ensure_store(db_path).get_agent_policy(agent_id, tenant_id=tenant_id)


def list_agent_policies(db_path: str | Path, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return _ensure_store(db_path).list_agent_policies(tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Risk policy storage
# ---------------------------------------------------------------------------

def upsert_risk_policy(db_path: str | Path, tenant_id: str, data: dict[str, Any]) -> None:
    _ensure_store(db_path).upsert_risk_policy(tenant_id, data)


def get_risk_policy(db_path: str | Path, tenant_id: str) -> dict[str, Any] | None:
    return _ensure_store(db_path).get_risk_policy(tenant_id)


def list_risk_policies(db_path: str | Path, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return _ensure_store(db_path).list_risk_policies(tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Compliance thresholds storage
# ---------------------------------------------------------------------------

def upsert_compliance_thresholds(db_path: str | Path, tenant_id: str, data: dict[str, Any]) -> None:
    _ensure_store(db_path).upsert_compliance_thresholds(tenant_id, data)


def get_compliance_thresholds(db_path: str | Path, tenant_id: str) -> dict[str, Any] | None:
    return _ensure_store(db_path).get_compliance_thresholds(tenant_id)


def list_compliance_thresholds(db_path: str | Path, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return _ensure_store(db_path).list_compliance_thresholds(tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Queue lock (advisory locking)
# ---------------------------------------------------------------------------

def acquire_queue_lock(
    db_path: str | Path,
    lock_name: str = "queue",
    holder_pid: int | None = None,
    ttl_seconds: int = 300,
) -> bool:
    return _ensure_store(db_path).acquire_queue_lock(lock_name=lock_name, holder_pid=holder_pid, ttl_seconds=ttl_seconds)


def release_queue_lock(
    db_path: str | Path,
    lock_name: str = "queue",
    holder_pid: int | None = None,
) -> bool:
    return _ensure_store(db_path).release_queue_lock(lock_name=lock_name, holder_pid=holder_pid)


def force_release_queue_lock(
    db_path: str | Path,
    lock_name: str = "queue",
) -> bool:
    return _ensure_store(db_path).force_release_queue_lock(lock_name=lock_name)


def get_queue_lock_info(
    db_path: str | Path,
    lock_name: str = "queue",
) -> dict[str, Any] | None:
    return _ensure_store(db_path).get_queue_lock_info(lock_name=lock_name)


# ---------------------------------------------------------------------------
# Webhook delivery dedup
# ---------------------------------------------------------------------------

def is_duplicate_delivery(db_path: str | Path, delivery_id: str) -> bool:
    return _ensure_store(db_path).is_duplicate_delivery(delivery_id)


def record_delivery(db_path: str | Path, delivery_id: str) -> None:
    _ensure_store(db_path).record_delivery(delivery_id)


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

def prune_events(db_path: str | Path, before: str, tenant_id: str | None = None, dry_run: bool = False) -> int:
    return _ensure_store(db_path).prune_events(before, tenant_id=tenant_id, dry_run=dry_run)
