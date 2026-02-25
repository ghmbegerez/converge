"""Pluggable embedding provider abstraction.

Providers implement a simple protocol: text in, vector out.
A deterministic test provider is included for CI (no external dependencies).

The DeterministicProvider uses SHA-256 hashing to produce vectors.  Identical
semantic text produces identical vectors (cosine similarity = 1.0), making it
suitable for detecting exact-duplicate intents in CI.  For *real* semantic
similarity (e.g. "add login page" ≈ "implement authentication screen"), use
SentenceTransformerProvider or another ML-based provider.
"""

from __future__ import annotations

import hashlib
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from converge.models import now_iso

# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

_DEFAULT_DIMENSION = 64
_DEFAULT_MODEL = "deterministic-v1"


@dataclass
class EmbeddingResult:
    """Result of embedding a single text."""
    vector: list[float]
    model: str
    dimension: int
    generated_at: str = field(default_factory=now_iso)


class EmbeddingProvider(ABC):
    """Protocol for embedding providers."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier for the model used (e.g. 'text-embedding-3-small')."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimension of the output vector."""

    @abstractmethod
    def embed(self, text: str) -> EmbeddingResult:
        """Embed a single text into a vector."""

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed multiple texts.  Default: sequential calls to embed()."""
        return [self.embed(t) for t in texts]


# ---------------------------------------------------------------------------
# Deterministic provider (for tests and CI)
# ---------------------------------------------------------------------------

class DeterministicProvider(EmbeddingProvider):
    """Hash-based deterministic provider.  Same text always yields same vector.

    Uses SHA-256 of the input text to generate a fixed-dimension vector.
    No external dependencies — suitable for CI and unit tests.
    """

    def __init__(self, dimension: int = _DEFAULT_DIMENSION):
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return _DEFAULT_MODEL

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> EmbeddingResult:
        vector = _hash_to_vector(text, self._dimension)
        return EmbeddingResult(
            vector=vector,
            model=self.model_name,
            dimension=self._dimension,
        )


def _hash_to_vector(text: str, dimension: int) -> list[float]:
    """Generate a deterministic unit-length vector from text via SHA-256 expansion."""
    # Expand hash bytes to fill the required dimension
    raw = b""
    i = 0
    while len(raw) < dimension * 4:  # 4 bytes per float32
        raw += hashlib.sha256(f"{text}:{i}".encode()).digest()
        i += 1
    # Unpack as float32 values, normalize to [-1, 1]
    floats = []
    for j in range(dimension):
        val = struct.unpack_from(">I", raw, j * 4)[0]
        floats.append((val / 2**32) * 2.0 - 1.0)
    # L2-normalize
    norm = sum(f * f for f in floats) ** 0.5
    if norm > 0:
        floats = [f / norm for f in floats]
    return floats


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "deterministic": DeterministicProvider,
}

# Auto-register sentence-transformers provider if available
try:
    from converge.semantic.sentence_transformer_provider import SentenceTransformerProvider

    _PROVIDERS["sentence-transformers"] = SentenceTransformerProvider
except ImportError:
    pass  # sentence-transformers not installed

_active_provider: EmbeddingProvider | None = None


def register_provider(name: str, cls: type[EmbeddingProvider]) -> None:
    """Register a custom embedding provider class."""
    _PROVIDERS[name] = cls


def get_provider(
    name: str = "deterministic",
    **kwargs: Any,
) -> EmbeddingProvider:
    """Get or create an embedding provider by name."""
    global _active_provider
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown embedding provider: {name!r}. Available: {list(_PROVIDERS)}")
    _active_provider = cls(**kwargs)
    return _active_provider


def active_provider() -> EmbeddingProvider | None:
    """Return the currently active provider, if any."""
    return _active_provider
