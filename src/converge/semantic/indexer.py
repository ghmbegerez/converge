"""Embedding indexer: generates and persists embeddings for intents.

Orchestrates the pipeline: intent → canonical text → checksum → embed → store.
Supports batch reindex with dry-run mode.
"""

from __future__ import annotations

import json
from typing import Any

from converge import event_log
from converge.defaults import QUERY_LIMIT_UNBOUNDED
from converge.models import Event, EventType, now_iso
from converge.semantic.canonical import build_canonical_text, canonical_checksum
from converge.semantic.embeddings import EmbeddingProvider, get_provider


def index_intent(
    intent_id: str,
    provider: EmbeddingProvider | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Generate and persist embedding for a single intent.

    Returns a result dict with status: 'indexed', 'skipped' (up-to-date), or 'error'.
    """
    if provider is None:
        provider = get_provider()

    intent = event_log.get_intent(intent_id)
    if intent is None:
        return {"intent_id": intent_id, "status": "error", "reason": "not_found"}

    # Build canonical text
    links = event_log.list_commit_links(intent_id)
    canonical = build_canonical_text(intent, commit_links=links)
    checksum = canonical_checksum(canonical)

    # Check if already up-to-date
    if not force:
        existing = event_log.get_embedding(intent_id, provider.model_name)
        if existing and existing["checksum"] == checksum:
            return {"intent_id": intent_id, "status": "skipped", "reason": "up_to_date"}

    # Generate embedding
    result = provider.embed(canonical)
    vector_json = json.dumps(result.vector)

    # Persist
    event_log.upsert_embedding(
        intent_id, provider.model_name, provider.dimension,
        checksum, vector_json, result.generated_at,
    )

    # Emit event
    event_log.append(Event(
        event_type=EventType.EMBEDDING_GENERATED,
        intent_id=intent_id,
        tenant_id=intent.tenant_id,
        payload={
            "model": provider.model_name,
            "dimension": provider.dimension,
            "checksum": checksum,
        },
        evidence={"canonical_length": len(canonical)},
    ))

    return {
        "intent_id": intent_id,
        "status": "indexed",
        "model": provider.model_name,
        "checksum": checksum,
    }


def reindex(
    *,
    provider_name: str = "deterministic",
    tenant_id: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Reindex embeddings for all intents (or per-tenant).

    Returns summary with counts for indexed, skipped, failed.
    """
    provider = get_provider(provider_name)
    intents = event_log.list_intents(
        tenant_id=tenant_id, limit=QUERY_LIMIT_UNBOUNDED,
    )

    stats = {"indexed": 0, "skipped": 0, "failed": 0, "total": len(intents)}
    failures: list[dict[str, Any]] = []

    for intent in intents:
        if dry_run:
            links = event_log.list_commit_links(intent.id)
            canonical = build_canonical_text(intent, commit_links=links)
            checksum = canonical_checksum(canonical)
            existing = event_log.get_embedding(intent.id, provider.model_name)
            if existing and existing["checksum"] == checksum and not force:
                stats["skipped"] += 1
            else:
                stats["indexed"] += 1  # would be indexed
            continue

        result = index_intent(intent.id, provider, force=force)
        status = result.get("status", "error")
        if status == "indexed":
            stats["indexed"] += 1
        elif status == "skipped":
            stats["skipped"] += 1
        else:
            stats["failed"] += 1
            failures.append(result)

    summary = {
        **stats,
        "model": provider.model_name,
        "dimension": provider.dimension,
        "dry_run": dry_run,
        "tenant_id": tenant_id,
        "timestamp": now_iso(),
    }
    if failures:
        summary["failures"] = failures

    # Emit reindex event (unless dry-run)
    if not dry_run:
        event_log.append(Event(
            event_type=EventType.EMBEDDING_REINDEXED,
            tenant_id=tenant_id,
            payload=summary,
            evidence={"total": stats["total"], "indexed": stats["indexed"]},
        ))

    return summary
