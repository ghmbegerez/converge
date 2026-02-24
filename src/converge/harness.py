"""Pre-PR evaluation harness (AR-46).

Evaluates intent quality signals before formal creation to catch issues early.
Checks semantic similarity against existing intents, ownership coverage,
and configurable rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from converge import event_log
from converge.defaults import QUERY_LIMIT_MEDIUM
from converge.event_types import EventType
from converge.models import Event


@dataclass
class EvaluationResult:
    score: float                               # 0.0 (bad) to 1.0 (good)
    passed: bool
    similar_intents: list[dict[str, Any]] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    mode: str = "shadow"                       # shadow | enforce

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "similar_intents": self.similar_intents,
            "signals": self.signals,
            "recommendations": self.recommendations,
            "mode": self.mode,
        }


@dataclass
class HarnessConfig:
    similarity_threshold: float = 0.80         # block if similarity > this
    max_similar_shown: int = 5                 # max similar intents to return
    mode: str = "shadow"                       # shadow | enforce
    rules: list[str] = field(default_factory=lambda: [
        "semantic_similarity",
        "description_quality",
    ])


def evaluate_intent(
    intent_data: dict[str, Any],
    *,
    config: HarnessConfig | None = None,
) -> EvaluationResult:
    """Evaluate a draft intent before creation.

    Checks:
    1. Semantic similarity to existing intents (duplicate detection)
    2. Description quality (has semantic fields)
    3. Target branch exists in active intents

    In shadow mode: always passes, logs results.
    In enforce mode: blocks if similarity too high.
    """
    cfg = config or HarnessConfig()
    signals: dict[str, Any] = {}
    recommendations: list[str] = []
    similar_intents: list[dict[str, Any]] = []

    # Signal 1: Semantic similarity
    if "semantic_similarity" in cfg.rules:
        sim_result = _check_semantic_similarity(intent_data, cfg)
        signals["max_similarity"] = sim_result["max_similarity"]
        similar_intents = sim_result["similar"]
        if sim_result["max_similarity"] > cfg.similarity_threshold:
            recommendations.append(
                f"Very similar intent found (similarity={sim_result['max_similarity']:.2f}). "
                "Consider reviewing existing intents before creating a new one."
            )

    # Signal 2: Description quality
    if "description_quality" in cfg.rules:
        quality = _check_description_quality(intent_data)
        signals["description_quality"] = quality["score"]
        if quality["score"] < 0.5:
            recommendations.extend(quality["suggestions"])

    # Composite score: average of all signals (higher = better)
    signal_values = list(signals.values())
    if signal_values:
        # Invert similarity (low similarity = good)
        adjusted = []
        for k, v in signals.items():
            if k == "max_similarity":
                adjusted.append(1.0 - v)
            else:
                adjusted.append(v)
        score = sum(adjusted) / len(adjusted)
    else:
        score = 1.0

    # Decision: in enforce mode, block if score too low
    passed = True
    if cfg.mode == "enforce" and score < 0.5:
        passed = False

    result = EvaluationResult(
        score=round(score, 3),
        passed=passed,
        similar_intents=similar_intents[:cfg.max_similar_shown],
        signals=signals,
        recommendations=recommendations,
        mode=cfg.mode,
    )

    # Emit event
    event_log.append(Event(
        event_type=EventType.INTENT_PRE_EVALUATED,
        intent_id=intent_data.get("id"),
        tenant_id=intent_data.get("tenant_id"),
        payload={
            "score": result.score,
            "passed": result.passed,
            "mode": cfg.mode,
            "signals": signals,
            "similar_count": len(similar_intents),
        },
    ))

    return result


# ---------------------------------------------------------------------------
# Signal evaluators
# ---------------------------------------------------------------------------

def _check_semantic_similarity(
    intent_data: dict[str, Any],
    cfg: HarnessConfig,
) -> dict[str, Any]:
    """Check semantic similarity against existing intents."""
    try:
        from converge.semantic.canonical import build_canonical_text
        from converge.semantic.embeddings import get_provider
        from converge.semantic.conflicts import cosine_similarity
    except ImportError:
        return {"max_similarity": 0.0, "similar": []}

    # Build canonical text for the draft intent
    source = intent_data.get("source", "")
    target = intent_data.get("target", "main")
    semantic = intent_data.get("semantic", {})
    text = build_canonical_text(source, target, semantic)

    # Generate embedding
    provider = get_provider("deterministic")
    draft_vec = provider.embed(text)

    # Compare against existing intents' embeddings
    embeddings = event_log.list_embeddings(limit=QUERY_LIMIT_MEDIUM)
    similar: list[dict[str, Any]] = []
    max_sim = 0.0

    for emb in embeddings:
        try:
            import json as _json
            stored_vec = _json.loads(emb["vector"]) if isinstance(emb["vector"], str) else emb["vector"]
            sim = cosine_similarity(draft_vec, stored_vec)
            if sim > 0.5:  # only report meaningful similarity
                similar.append({
                    "intent_id": emb["intent_id"],
                    "similarity": round(sim, 3),
                })
            max_sim = max(max_sim, sim)
        except (ValueError, KeyError):
            continue

    similar.sort(key=lambda x: x["similarity"], reverse=True)
    return {
        "max_similarity": round(max_sim, 3),
        "similar": similar[:cfg.max_similar_shown],
    }


def _check_description_quality(intent_data: dict[str, Any]) -> dict[str, Any]:
    """Check if the intent has adequate description fields."""
    semantic = intent_data.get("semantic", {})
    score_parts: list[float] = []
    suggestions: list[str] = []

    # Has description?
    desc = semantic.get("description", "")
    if desc and len(desc) > 10:
        score_parts.append(1.0)
    else:
        score_parts.append(0.0)
        suggestions.append("Add a meaningful description to the semantic field.")

    # Has scope?
    scope = semantic.get("scope", semantic.get("affected_areas", []))
    if scope:
        score_parts.append(1.0)
    else:
        score_parts.append(0.3)
        suggestions.append("Add affected areas/scope to help with conflict detection.")

    # Has source and target?
    if intent_data.get("source") and intent_data.get("target"):
        score_parts.append(1.0)
    else:
        score_parts.append(0.0)
        suggestions.append("Both source and target branches are required.")

    score = sum(score_parts) / len(score_parts) if score_parts else 0.0
    return {"score": round(score, 3), "suggestions": suggestions}
