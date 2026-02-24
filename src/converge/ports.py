"""Storage port interfaces for Converge.

Defines Protocol classes that any persistence backend must implement.
The composite ``ConvergeStore`` is what application code depends on.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from converge.models import Event, Intent, ReviewTask, SecurityFinding, Status


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
        source: str | None = None,
        limit: int = 200,
    ) -> list[Intent]: ...
    def update_intent_status(
        self,
        intent_id: str,
        status: Status,
        retries: int | None = None,
    ) -> None: ...


@runtime_checkable
class CommitLinkStorePort(Protocol):
    def upsert_commit_link(
        self, intent_id: str, repo: str, sha: str, role: str, observed_at: str,
    ) -> None: ...
    def list_commit_links(self, intent_id: str) -> list[dict[str, Any]]: ...
    def delete_commit_link(
        self, intent_id: str, sha: str, role: str,
    ) -> bool: ...


@runtime_checkable
class EmbeddingStorePort(Protocol):
    def upsert_embedding(
        self, intent_id: str, model: str, dimension: int,
        checksum: str, vector: str, generated_at: str,
    ) -> None: ...
    def get_embedding(
        self, intent_id: str, model: str,
    ) -> dict[str, Any] | None: ...
    def list_embeddings(
        self, *, tenant_id: str | None = None, model: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]: ...
    def delete_embedding(self, intent_id: str, model: str) -> bool: ...
    def embedding_coverage(
        self, *, tenant_id: str | None = None, model: str | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ReviewStorePort(Protocol):
    def upsert_review_task(self, task: ReviewTask) -> None: ...
    def get_review_task(self, task_id: str) -> ReviewTask | None: ...
    def list_review_tasks(
        self,
        *,
        intent_id: str | None = None,
        status: str | None = None,
        reviewer: str | None = None,
        tenant_id: str | None = None,
        limit: int = 200,
    ) -> list[ReviewTask]: ...
    def update_review_task_status(
        self, task_id: str, status: str, **fields: Any,
    ) -> None: ...


@runtime_checkable
class IntakeStorePort(Protocol):
    def upsert_intake_override(
        self, tenant_id: str, mode: str, set_by: str, reason: str,
    ) -> None: ...
    def get_intake_override(self, tenant_id: str) -> dict[str, Any] | None: ...
    def delete_intake_override(self, tenant_id: str) -> bool: ...


@runtime_checkable
class SecurityScannerPort(Protocol):
    """Port for security scanner adapters.

    Each adapter wraps a specific tool (bandit, pip-audit, gitleaks) and
    normalizes its output into SecurityFinding instances.
    """
    @property
    def scanner_name(self) -> str: ...
    def scan(self, path: str, **options: Any) -> list[SecurityFinding]: ...
    def is_available(self) -> bool: ...


@runtime_checkable
class SecurityFindingStorePort(Protocol):
    def upsert_security_finding(self, finding: dict[str, Any]) -> None: ...
    def list_security_findings(
        self,
        *,
        intent_id: str | None = None,
        scanner: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        tenant_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]: ...
    def count_security_findings(
        self,
        *,
        intent_id: str | None = None,
        severity: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, int]: ...


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


@runtime_checkable
class ChainStatePort(Protocol):
    def get_chain_state(self, chain_id: str = "main") -> dict[str, Any] | None: ...
    def save_chain_state(self, chain_id: str, last_hash: str, event_count: int) -> None: ...


# ---------------------------------------------------------------------------
# Composite store
# ---------------------------------------------------------------------------

@runtime_checkable
class ConvergeStore(
    EventStorePort,
    IntentStorePort,
    CommitLinkStorePort,
    EmbeddingStorePort,
    ReviewStorePort,
    IntakeStorePort,
    SecurityFindingStorePort,
    PolicyStorePort,
    LockPort,
    DeliveryPort,
    ChainStatePort,
    Protocol,
):
    def close(self) -> None: ...
