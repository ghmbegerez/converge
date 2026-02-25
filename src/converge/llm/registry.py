"""LLM adapter registry with rate limiting."""

from __future__ import annotations

import logging
import os
import time

from converge.llm.null_adapter import NullLLMAdapter
from converge.llm.port import LLMPort

log = logging.getLogger("converge.llm.registry")

_adapter: LLMPort | None = None
_call_timestamps: list[float] = []
MAX_CALLS_PER_HOUR = int(os.environ.get("CONVERGE_LLM_RATE_LIMIT", "30"))


def get_adapter() -> LLMPort:
    """Get or create the configured LLM adapter."""
    global _adapter
    if _adapter is not None:
        return _adapter

    provider = os.environ.get("CONVERGE_LLM_PROVIDER", "null")
    api_key = os.environ.get("CONVERGE_LLM_API_KEY", "")
    model = os.environ.get("CONVERGE_LLM_MODEL", "")

    if provider == "anthropic" and api_key:
        try:
            from converge.llm.anthropic_adapter import AnthropicLLMAdapter

            _adapter = AnthropicLLMAdapter(api_key, model or "claude-sonnet-4-20250514")
        except ImportError:
            log.warning("anthropic package not installed — falling back to null adapter")
            _adapter = NullLLMAdapter()
    elif provider == "openai" and api_key:
        try:
            from converge.llm.openai_adapter import OpenAILLMAdapter

            _adapter = OpenAILLMAdapter(api_key, model or "gpt-4o")
        except ImportError:
            log.warning("openai package not installed — falling back to null adapter")
            _adapter = NullLLMAdapter()
    else:
        _adapter = NullLLMAdapter()

    return _adapter


def check_rate_limit() -> bool:
    """Return True if under the hourly call limit."""
    now = time.time()
    _call_timestamps[:] = [t for t in _call_timestamps if now - t < 3600]
    return len(_call_timestamps) < MAX_CALLS_PER_HOUR


def record_call() -> None:
    """Record an LLM call for rate limiting."""
    _call_timestamps.append(time.time())


def reset_adapter() -> None:
    """Reset the adapter and rate-limit state (for tests)."""
    global _adapter
    _adapter = None
    _call_timestamps.clear()
