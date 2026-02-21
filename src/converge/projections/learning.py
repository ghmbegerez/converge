"""Structured learning: actionable lessons with observed vs target metrics."""

from __future__ import annotations

from typing import Any

# --- Health learning thresholds ---
_HEALTH_STRONG = 70
_HEALTH_ACCEPTABLE = 40
_MERGEABLE_TARGET = 0.85
_MERGEABLE_CRITICAL = 0.7
_ENTROPY_TARGET = 15.0
_ENTROPY_HIGH = 30.0
_REJECTION_TARGET = 3

# --- Change learning thresholds ---
_RISK_SAFE = 40.0
_RISK_HIGH = 60.0
_ENTROPY_CHANGE_TARGET = 20.0
_ENTROPY_CHANGE_HIGH = 40.0

_MAX_NEXT_ACTIONS = 3


def _health_level(score: float) -> str:
    """Classify health score into strong/acceptable/fragile."""
    if score >= _HEALTH_STRONG:
        return "strong"
    if score >= _HEALTH_ACCEPTABLE:
        return "acceptable"
    return "fragile"


def _build_learning_result(
    summary: str, level: str, lessons: list[dict[str, Any]],
) -> dict[str, Any]:
    """Sort lessons by priority and assemble the standard learning result."""
    lessons.sort(key=lambda l: l["priority"])
    next_actions = [l["action"] for l in lessons[:_MAX_NEXT_ACTIONS]]
    return {"summary": summary, "level": level, "lessons": lessons, "next_actions": next_actions}


def _lesson(code: str, title: str, why: str, action: str, priority: int,
            metric: str, observed: float, target: float) -> dict[str, Any]:
    """Structured lesson with observed vs target — consumable by agents."""
    return {
        "code": code,
        "title": title,
        "why": why,
        "action": action,
        "priority": priority,
        "metric": {"name": metric, "observed": round(observed, 3), "target": round(target, 3)},
    }


def derive_health_learning(
    health_score: float,
    mergeable_rate: float,
    avg_entropy: float,
    rejected_count: int,
) -> dict[str, Any]:
    level = _health_level(health_score)
    summary = f"Repo health is {level} (score: {health_score:.0f})"
    lessons = []

    if mergeable_rate < _MERGEABLE_TARGET:
        lessons.append(_lesson(
            code="learn.low_mergeable",
            title="Low mergeable rate",
            why="A low rate increases friction and integration queue backlog",
            action="Reduce average change size and enforce pre-merge checks",
            priority=1 if mergeable_rate < _MERGEABLE_CRITICAL else 2,
            metric="mergeable_rate", observed=mergeable_rate, target=_MERGEABLE_TARGET,
        ))
    if avg_entropy > _ENTROPY_TARGET:
        lessons.append(_lesson(
            code="learn.high_entropy",
            title="Elevated entropy",
            why="High average entropy indicates large or complex changes entering the system",
            action="Split large intents into smaller focused changes",
            priority=1 if avg_entropy > _ENTROPY_HIGH else 2,
            metric="avg_entropy", observed=avg_entropy, target=_ENTROPY_TARGET,
        ))
    if rejected_count > _REJECTION_TARGET:
        lessons.append(_lesson(
            code="learn.frequent_rejections",
            title="Frequent rejections",
            why="Multiple rejections indicate systemic issues with source branch quality or policy fit",
            action="Review policy thresholds and source branch preparation workflows",
            priority=1,
            metric="rejected_count", observed=float(rejected_count), target=float(_REJECTION_TARGET),
        ))
    if health_score < _HEALTH_STRONG:
        lessons.append(_lesson(
            code="learn.health_below_target",
            title="Health below target",
            why="Overall repo health has degraded below the safe threshold",
            action="Prioritize resolving conflicts, reducing entropy, and clearing the queue",
            priority=0,
            metric="health_score", observed=health_score, target=float(_HEALTH_STRONG),
        ))

    return _build_learning_result(summary, level, lessons)


def derive_change_learning(
    health_score: float,
    risk_score: float,
    entropy: float,
    mergeable: bool,
) -> dict[str, Any]:
    lessons = []
    if not mergeable:
        lessons.append(_lesson(
            code="learn.conflict",
            title="Merge conflict present",
            why="Source branch has conflicts with target — cannot merge cleanly",
            action="Rebase or resolve conflicts before retrying",
            priority=0,
            metric="mergeable", observed=0.0, target=1.0,
        ))
    if risk_score > _RISK_SAFE:
        lessons.append(_lesson(
            code="learn.high_risk",
            title="Elevated risk score",
            why="Risk score exceeds safe threshold — multiple risk signals contributing",
            action="Consider splitting into smaller changes or adding test coverage",
            priority=1 if risk_score > _RISK_HIGH else 2,
            metric="risk_score", observed=risk_score, target=_RISK_SAFE,
        ))
    if entropy > _ENTROPY_CHANGE_TARGET:
        lessons.append(_lesson(
            code="learn.change_entropy",
            title="High change entropy",
            why="Entropic load indicates a complex or wide-reaching change",
            action="Reduce scope or break into incremental, independently-mergeable changes",
            priority=1 if entropy > _ENTROPY_CHANGE_HIGH else 2,
            metric="entropy_score", observed=entropy, target=_ENTROPY_CHANGE_TARGET,
        ))

    level = _health_level(health_score)
    return _build_learning_result(f"Change health: {level} ({health_score:.0f})", level, lessons)
