"""Structured learning: actionable lessons with observed vs target metrics."""

from __future__ import annotations

from typing import Any


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
    level = "strong" if health_score >= 70 else ("acceptable" if health_score >= 40 else "fragile")
    summary = f"Repo health is {level} (score: {health_score:.0f})"
    lessons = []

    if mergeable_rate < 0.85:
        lessons.append(_lesson(
            code="learn.low_mergeable",
            title="Low mergeable rate",
            why="A low rate increases friction and integration queue backlog",
            action="Reduce average change size and enforce pre-merge checks",
            priority=1 if mergeable_rate < 0.7 else 2,
            metric="mergeable_rate", observed=mergeable_rate, target=0.85,
        ))
    if avg_entropy > 15:
        lessons.append(_lesson(
            code="learn.high_entropy",
            title="Elevated entropy",
            why="High average entropy indicates large or complex changes entering the system",
            action="Split large intents into smaller focused changes",
            priority=1 if avg_entropy > 30 else 2,
            metric="avg_entropy", observed=avg_entropy, target=15.0,
        ))
    if rejected_count > 3:
        lessons.append(_lesson(
            code="learn.frequent_rejections",
            title="Frequent rejections",
            why="Multiple rejections indicate systemic issues with source branch quality or policy fit",
            action="Review policy thresholds and source branch preparation workflows",
            priority=1,
            metric="rejected_count", observed=float(rejected_count), target=3.0,
        ))
    if health_score < 70:
        lessons.append(_lesson(
            code="learn.health_below_target",
            title="Health below target",
            why="Overall repo health has degraded below the safe threshold",
            action="Prioritize resolving conflicts, reducing entropy, and clearing the queue",
            priority=0,
            metric="health_score", observed=health_score, target=70.0,
        ))

    lessons.sort(key=lambda l: l["priority"])
    next_actions = [l["action"] for l in lessons[:3]]
    return {"summary": summary, "level": level, "lessons": lessons, "next_actions": next_actions}


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
    if risk_score > 40:
        lessons.append(_lesson(
            code="learn.high_risk",
            title="Elevated risk score",
            why="Risk score exceeds safe threshold — multiple risk signals contributing",
            action="Consider splitting into smaller changes or adding test coverage",
            priority=1 if risk_score > 60 else 2,
            metric="risk_score", observed=risk_score, target=40.0,
        ))
    if entropy > 20:
        lessons.append(_lesson(
            code="learn.change_entropy",
            title="High change entropy",
            why="Entropic load indicates a complex or wide-reaching change",
            action="Reduce scope or break into incremental, independently-mergeable changes",
            priority=1 if entropy > 40 else 2,
            metric="entropy_score", observed=entropy, target=20.0,
        ))

    level = "strong" if health_score >= 70 else ("acceptable" if health_score >= 40 else "fragile")
    lessons.sort(key=lambda l: l["priority"])
    next_actions = [l["action"] for l in lessons[:3]]
    return {"summary": f"Change health: {level} ({health_score:.0f})", "level": level,
            "lessons": lessons, "next_actions": next_actions}
