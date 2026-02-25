"""Anthropic Claude adapter for LLM-powered review analysis."""

from __future__ import annotations

import json
import logging
from typing import Any

from converge.llm.port import ReviewAnalysis

log = logging.getLogger("converge.llm.anthropic")


def _build_review_prompt(
    intent_data: dict[str, Any],
    diff: str,
    risk_data: dict[str, Any],
) -> str:
    """Build the review analysis prompt."""
    return (
        "You are a code review advisor. Analyze the following intent and risk data, "
        "then provide a structured review summary.\n\n"
        f"## Intent\n```json\n{json.dumps(intent_data, indent=2, default=str)}\n```\n\n"
        f"## Risk Data\n```json\n{json.dumps(risk_data, indent=2, default=str)}\n```\n\n"
        f"## Diff\n```\n{diff[:4000]}\n```\n\n"
        "Respond in JSON with keys: summary (string), risk_highlights (list of strings), "
        "suggested_focus_areas (list of strings), confidence (float 0-1)."
    )


def _parse_response(text: str) -> ReviewAnalysis:
    """Parse LLM response into ReviewAnalysis."""
    try:
        # Try to extract JSON from response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            return ReviewAnalysis(
                summary=data.get("summary", text[:500]),
                risk_highlights=data.get("risk_highlights", []),
                suggested_focus_areas=data.get("suggested_focus_areas", []),
                confidence=float(data.get("confidence", 0.5)),
            )
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: use raw text as summary
    return ReviewAnalysis(summary=text[:500], confidence=0.3)


class AnthropicLLMAdapter:
    """Anthropic Claude adapter for review analysis."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def analyze_review(
        self,
        intent_data: dict[str, Any],
        diff: str,
        risk_data: dict[str, Any],
    ) -> ReviewAnalysis:
        prompt = _build_review_prompt(intent_data, diff, risk_data)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_response(response.content[0].text)

    def is_available(self) -> bool:
        return self._client is not None
