"""OpenAI adapter for LLM-powered review analysis."""

from __future__ import annotations

from typing import Any

from converge.llm.port import ReviewAnalysis
from converge.llm.prompts import build_review_prompt, parse_review_response


class OpenAILLMAdapter:
    """OpenAI adapter for review analysis."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        import openai

        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    @property
    def provider_name(self) -> str:
        return "openai"

    def analyze_review(
        self,
        intent_data: dict[str, Any],
        diff: str,
        risk_data: dict[str, Any],
    ) -> ReviewAnalysis:
        prompt = build_review_prompt(intent_data, diff, risk_data)
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return parse_review_response(response.choices[0].message.content or "")

    def is_available(self) -> bool:
        return self._client is not None
