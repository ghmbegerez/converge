"""Storage port interfaces for Converge.

Defines Protocol classes that any persistence backend must implement.
The composite ``ConvergeStore`` is what application code depends on.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from converge.models import Event, Intent, Status


# ---------------------------------------------------------------------------
# Individual ports
# ---------------------------------------------------------------------------

@runtime_checkable
class EventStorePort(Protocol):
    def append(self, event: Event) -> Event: ...
    def query(
        self,
        *,
        event_type: str | None = None,
        intent_id: str | None = None,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]: ...
    def count(self, **filters: Any) -> int: ...
    def prune_events(
        self,
        before: str,
        tenant_id: str | None = None,
        dry_run: bool = False,
    ) -> int: ...


@runtime_checkable
class IntentStorePort(Protocol):
    def upsert_intent(self, intent: Intent) -> None: ...
    def get_intent(self, intent_id: str) -> Intent | None: ...
    def list_intents(
        self,
        *,
        status: str | None = None,
        tenant_id: str | None = None,
        limit: int = 200,
    ) -> list[Intent]: ...
    def update_intent_status(
        self,
        intent_id: str,
        status: Status,
        retries: int | None = None,
    ) -> None: ...


@runtime_checkable
class PolicyStorePort(Protocol):
    def upsert_agent_policy(self, data: dict[str, Any]) -> None: ...
    def get_agent_policy(
        self, agent_id: str, tenant_id: str | None = None,
    ) -> dict[str, Any] | None: ...
    def list_agent_policies(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]: ...
    def upsert_risk_policy(
        self, tenant_id: str, data: dict[str, Any],
    ) -> None: ...
    def get_risk_policy(self, tenant_id: str) -> dict[str, Any] | None: ...
    def list_risk_policies(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]: ...
    def upsert_compliance_thresholds(
        self, tenant_id: str, data: dict[str, Any],
    ) -> None: ...
    def get_compliance_thresholds(
        self, tenant_id: str,
    ) -> dict[str, Any] | None: ...
    def list_compliance_thresholds(
        self, tenant_id: str | None = None,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class LockPort(Protocol):
    def acquire_queue_lock(
        self,
        lock_name: str = "queue",
        holder_pid: int | None = None,
        ttl_seconds: int = 300,
    ) -> bool: ...
    def release_queue_lock(
        self,
        lock_name: str = "queue",
        holder_pid: int | None = None,
    ) -> bool: ...
    def force_release_queue_lock(
        self, lock_name: str = "queue",
    ) -> bool: ...
    def get_queue_lock_info(
        self, lock_name: str = "queue",
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class DeliveryPort(Protocol):
    def is_duplicate_delivery(self, delivery_id: str) -> bool: ...
    def record_delivery(self, delivery_id: str) -> None: ...


# ---------------------------------------------------------------------------
# Composite store
# ---------------------------------------------------------------------------

@runtime_checkable
class ConvergeStore(
    EventStorePort,
    IntentStorePort,
    PolicyStorePort,
    LockPort,
    DeliveryPort,
    Protocol,
):
    def close(self) -> None: ...
