"""LLM port: protocol definition for review analysis adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ReviewAnalysis:
    """Result of LLM-powered review analysis."""

    summary: str
    risk_highlights: list[str] = field(default_factory=list)
    suggested_focus_areas: list[str] = field(default_factory=list)
    confidence: float = 0.0  # 0-1


@runtime_checkable
class LLMPort(Protocol):
    """Protocol for LLM adapters used in review analysis."""

    @property
    def provider_name(self) -> str: ...

    def analyze_review(
        self,
        intent_data: dict[str, Any],
        diff: str,
        risk_data: dict[str, Any],
    ) -> ReviewAnalysis: ...

    def is_available(self) -> bool: ...
