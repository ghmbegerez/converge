"""Shared prompt construction and response parsing for LLM adapters."""

from __future__ import annotations

import json
import logging
from typing import Any

from converge.llm.port import ReviewAnalysis

log = logging.getLogger("converge.llm")


def build_review_prompt(
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


def parse_review_response(text: str) -> ReviewAnalysis:
    """Parse LLM response into ReviewAnalysis."""
    try:
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
        log.debug("Failed to parse JSON from LLM response, using raw text fallback")
    return ReviewAnalysis(summary=text[:500], confidence=0.3)
