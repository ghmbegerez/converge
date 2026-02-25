"""Null LLM adapter: no-op default when no LLM is configured."""

from __future__ import annotations

from typing import Any

from converge.llm.port import ReviewAnalysis


class NullLLMAdapter:
    """No-op adapter. Default when no LLM is configured."""

    @property
    def provider_name(self) -> str:
        return "null"

    def analyze_review(
        self,
        intent_data: dict[str, Any],
        diff: str,
        risk_data: dict[str, Any],
    ) -> ReviewAnalysis:
        return ReviewAnalysis(summary="", confidence=0.0)

    def is_available(self) -> bool:
        return False
