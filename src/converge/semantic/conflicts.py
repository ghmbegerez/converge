"""Semantic conflict detection between intents.

Detects when intents from different plans target the same branch and have
high semantic similarity — indicating potential merge conflicts or
duplicated work.

Pipeline:
  1. Candidate generation: same target branch, different plan_id, active status
  2. Embedding similarity: cosine distance between intent embeddings
  3. Scoring heuristics: combine similarity, target overlap, coupling overlap
  4. Eventing: emit detected/resolved events, respect shadow/enforce mode
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import logging

from converge import event_log
from converge.defaults import QUERY_LIMIT_LARGE
from converge.models import Event, EventType, Intent, Status, now_iso

log = logging.getLogger("converge.semantic.conflicts")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ACTIVE_STATUSES = frozenset({Status.READY.value, Status.VALIDATED.value, Status.QUEUED.value})

# Thresholds are calibrated per provider type:
# - Deterministic (hash-based): identical text → cosine ~1.0, different text → cosine ~0.0.
#   Use high thresholds since the provider only detects exact-duplicate semantic text.
# - Semantic (ML-based): similar meaning → cosine 0.6-0.9.
#   Lower thresholds catch related intents even with different wording.
_DEFAULT_SIMILARITY_THRESHOLD = 0.70
_DEFAULT_CONFLICT_THRESHOLD = 0.60
_DETERMINISTIC_SIMILARITY_THRESHOLD = 0.95
_DETERMINISTIC_CONFLICT_THRESHOLD = 0.80


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ConflictCandidate:
    """A pair of intents that are candidates for conflict."""
    intent_a: str
    intent_b: str
    similarity: float
    target: str


@dataclass
class ConflictScore:
    """Scored conflict with heuristic breakdown."""
    intent_a: str
    intent_b: str
    score: float
    similarity: float
    target_overlap: float
    scope_overlap: float
    target: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConflictReport:
    """Full conflict scan result."""
    conflicts: list[ConflictScore]
    candidates_checked: int
    mode: str  # shadow | enforce
    threshold: float
    timestamp: str = field(default_factory=now_iso)


# ---------------------------------------------------------------------------
# Vector math
# ---------------------------------------------------------------------------

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Uses numpy when available for faster computation on large vectors;
    falls back to pure-Python implementation otherwise.
    """
    if len(a) != len(b) or not a:
        return 0.0
    try:
        import numpy as np

        va, vb = np.array(a), np.array(b)
        dot = np.dot(va, vb)
        norm = np.linalg.norm(va) * np.linalg.norm(vb)
        return float(dot / norm) if norm > 0 else 0.0
    except ImportError:
        # Pure Python fallback
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Candidate generation (AR-18)
# ---------------------------------------------------------------------------

def _load_active_intents(
    *,
    tenant_id: str | None = None,
    target: str | None = None,
) -> list[Intent]:
    """Load active intents (READY/VALIDATED/QUEUED)."""
    result: list[Intent] = []
    for status_val in _ACTIVE_STATUSES:
        intents = event_log.list_intents(
        status=status_val, tenant_id=tenant_id, limit=QUERY_LIMIT_LARGE,
        )
        result.extend(intents)
    if target:
        result = [i for i in result if i.target == target]
    return result


def _load_embedding_vectors(
    intent_ids: list[str],
    model: str,
) -> dict[str, list[float]]:
    """Load embedding vectors for a set of intents. Returns {intent_id: vector}."""
    vectors: dict[str, list[float]] = {}
    for iid in intent_ids:
        emb = event_log.get_embedding(iid, model)
        if emb and emb.get("vector"):
            vec = emb["vector"]
            if isinstance(vec, str):
                vec = json.loads(vec)
            vectors[iid] = vec
    return vectors


def _effective_similarity_threshold(model: str, explicit: float | None) -> float:
    """Return the effective similarity threshold for the given model."""
    if explicit is not None:
        return explicit
    if model.startswith("deterministic"):
        return _DETERMINISTIC_SIMILARITY_THRESHOLD
    return _DEFAULT_SIMILARITY_THRESHOLD


def generate_candidates(
    *,
    model: str = "deterministic-v1",
    tenant_id: str | None = None,
    target: str | None = None,
    similarity_threshold: float | None = None,
) -> list[ConflictCandidate]:
    """Find pairs of intents with high semantic similarity across different plans.

    Intents sharing the same plan_id are excluded from comparison (intra-plan
    coherence is the generator's responsibility).

    When *similarity_threshold* is ``None`` (the default), the threshold is
    auto-selected based on the provider: 0.95 for hash-based deterministic
    vectors (which are either ~1.0 or ~0.0) and 0.70 for ML-based embeddings.
    """
    effective_threshold = _effective_similarity_threshold(model, similarity_threshold)

    intents = _load_active_intents(tenant_id=tenant_id, target=target)
    if len(intents) < 2:
        return []

    # Group by target branch for efficient comparison
    by_target: dict[str, list[Intent]] = {}
    for intent in intents:
        by_target.setdefault(intent.target, []).append(intent)

    # Load all embeddings
    all_ids = [i.id for i in intents]
    vectors = _load_embedding_vectors(all_ids, model)

    candidates: list[ConflictCandidate] = []
    seen: set[tuple[str, str]] = set()

    for tgt, group in by_target.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                # Skip same plan_id (intra-plan coherence)
                if a.plan_id and b.plan_id and a.plan_id == b.plan_id:
                    continue
                # Skip if both have no plan_id (unplanned intents)
                # — these are independent human intents, compare them
                pair = tuple(sorted((a.id, b.id)))
                if pair in seen:
                    continue
                seen.add(pair)

                va = vectors.get(a.id)
                vb = vectors.get(b.id)
                if va is None or vb is None:
                    missing = [x for x, v in ((a.id, va), (b.id, vb)) if v is None]
                    log.warning("Missing embedding for %s — skipping conflict check", missing)
                    continue

                sim = _cosine_similarity(va, vb)
                if sim >= effective_threshold:
                    candidates.append(ConflictCandidate(
                        intent_a=a.id,
                        intent_b=b.id,
                        similarity=round(sim, 4),
                        target=tgt,
                    ))

    # Sort by similarity descending
    candidates.sort(key=lambda c: c.similarity, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Scoring heuristics (AR-19)
# ---------------------------------------------------------------------------

def _scope_overlap(a: Intent, b: Intent) -> float:
    """Fraction of scope hints shared between two intents."""
    scope_a = set(a.technical.get("scope_hint", []))
    scope_b = set(b.technical.get("scope_hint", []))
    if not scope_a and not scope_b:
        return 0.0
    union = scope_a | scope_b
    if not union:
        return 0.0
    return len(scope_a & scope_b) / len(union)


def _target_overlap(a: Intent, b: Intent) -> float:
    """1.0 if same target branch, 0.0 otherwise."""
    return 1.0 if a.target == b.target else 0.0


def score_conflict(
    candidate: ConflictCandidate,
    intent_a: Intent,
    intent_b: Intent,
    *,
    w_similarity: float = 0.6,
    w_target: float = 0.2,
    w_scope: float = 0.2,
) -> ConflictScore:
    """Score a conflict candidate using weighted heuristics.

    Weights default to: 60% embedding similarity, 20% target overlap, 20% scope overlap.
    """
    target_ov = _target_overlap(intent_a, intent_b)
    scope_ov = _scope_overlap(intent_a, intent_b)

    score = (
        w_similarity * candidate.similarity
        + w_target * target_ov
        + w_scope * scope_ov
    )

    return ConflictScore(
        intent_a=candidate.intent_a,
        intent_b=candidate.intent_b,
        score=round(score, 4),
        similarity=candidate.similarity,
        target_overlap=target_ov,
        scope_overlap=scope_ov,
        target=candidate.target,
        details={
            "w_similarity": w_similarity,
            "w_target": w_target,
            "w_scope": w_scope,
            "plan_a": intent_a.plan_id,
            "plan_b": intent_b.plan_id,
            "origin_a": intent_a.origin_type,
            "origin_b": intent_b.origin_type,
        },
    )


# ---------------------------------------------------------------------------
# Conflict scan (AR-20)
# ---------------------------------------------------------------------------

def scan_conflicts(
    *,
    model: str = "deterministic-v1",
    tenant_id: str | None = None,
    target: str | None = None,
    similarity_threshold: float | None = None,
    conflict_threshold: float | None = None,
    mode: str = "shadow",
) -> ConflictReport:
    """Run full conflict scan: generate candidates, score, emit events.

    Modes:
      - shadow: detect and log conflicts, do not block
      - enforce: detect, log, and mark as actionable (could gate queue processing)
    """
    effective_conflict = conflict_threshold
    if effective_conflict is None:
        if model.startswith("deterministic"):
            effective_conflict = _DETERMINISTIC_CONFLICT_THRESHOLD
        else:
            effective_conflict = _DEFAULT_CONFLICT_THRESHOLD

    candidates = generate_candidates(
        model=model,
        tenant_id=tenant_id,
        target=target,
        similarity_threshold=similarity_threshold,
    )

    scored: list[ConflictScore] = []
    for cand in candidates:
        intent_a = event_log.get_intent(cand.intent_a)
        intent_b = event_log.get_intent(cand.intent_b)
        if intent_a is None or intent_b is None:
            continue

        cs = score_conflict(cand, intent_a, intent_b)
        if cs.score >= effective_conflict:
            scored.append(cs)

            # Emit conflict event
            event_log.append(Event(
                event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
                intent_id=cs.intent_a,
                tenant_id=tenant_id,
                payload={
                    "intent_a": cs.intent_a,
                    "intent_b": cs.intent_b,
                    "score": cs.score,
                    "similarity": cs.similarity,
                    "target_overlap": cs.target_overlap,
                    "scope_overlap": cs.scope_overlap,
                    "target": cs.target,
                    "mode": mode,
                },
                evidence={
                    "plan_a": cs.details.get("plan_a"),
                    "plan_b": cs.details.get("plan_b"),
                    "conflict_threshold": conflict_threshold,
                },
            ))

    return ConflictReport(
        conflicts=scored,
        candidates_checked=len(candidates),
        mode=mode,
        threshold=effective_conflict,
    )


def resolve_conflict(
    intent_a: str,
    intent_b: str,
    *,
    resolution: str = "acknowledged",
    resolved_by: str = "system",
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Mark a conflict pair as resolved."""
    event_log.append(Event(
        event_type=EventType.SEMANTIC_CONFLICT_RESOLVED,
        intent_id=intent_a,
        tenant_id=tenant_id,
        payload={
            "intent_a": intent_a,
            "intent_b": intent_b,
            "resolution": resolution,
            "resolved_by": resolved_by,
        },
    ))
    return {
        "ok": True,
        "intent_a": intent_a,
        "intent_b": intent_b,
        "resolution": resolution,
    }


def list_conflicts(
    *,
    tenant_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List recent conflict events (detected, not yet resolved)."""
    detected = event_log.query(
        event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
        tenant_id=tenant_id,
        limit=limit,
    )
    # Filter out resolved pairs
    resolved_pairs: set[tuple[str, str]] = set()
    resolved_events = event_log.query(
        event_type=EventType.SEMANTIC_CONFLICT_RESOLVED,
        tenant_id=tenant_id,
        limit=limit * 2,
    )
    for ev in resolved_events:
        p = ev.get("payload", {})
        pair = tuple(sorted((p.get("intent_a", ""), p.get("intent_b", ""))))
        resolved_pairs.add(pair)

    result = []
    for ev in detected:
        p = ev.get("payload", {})
        pair = tuple(sorted((p.get("intent_a", ""), p.get("intent_b", ""))))
        if pair not in resolved_pairs:
            result.append(p)
    return result
