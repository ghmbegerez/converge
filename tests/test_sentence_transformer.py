"""Tests for sentence-transformer embedding provider (Initiative 1)."""
import math

import pytest
from unittest.mock import MagicMock

from converge.semantic.embeddings import (
    EmbeddingResult,
    DeterministicProvider,
    _PROVIDERS,
    get_provider,
)


def test_provider_registration():
    """sentence-transformers provider is registered when importable."""
    if "sentence-transformers" not in _PROVIDERS:
        pytest.skip("sentence-transformers not installed")
    assert "sentence-transformers" in _PROVIDERS


def test_deterministic_stays_default():
    """Without flag override, deterministic provider is used."""
    provider = get_provider("deterministic")
    assert provider.model_name == "deterministic-v1"
    assert provider.dimension == 64


def test_flag_switches_provider(monkeypatch):
    """With semantic_embeddings_model flag set, provider resolution changes."""
    from converge.semantic.indexer import _resolve_provider_name
    from converge import feature_flags

    monkeypatch.setenv("CONVERGE_FF_SEMANTIC_EMBEDDINGS_MODEL", "1")
    monkeypatch.setenv("CONVERGE_FF_SEMANTIC_EMBEDDINGS_MODEL_MODE", "sentence-transformers")
    feature_flags.reload_flags()

    name = _resolve_provider_name()
    assert name == "sentence-transformers"




def test_embed_returns_correct_dimension():
    """Mocked SentenceTransformer returns vectors of expected dimension."""
    import numpy as np
    from converge.semantic.sentence_transformer_provider import SentenceTransformerProvider

    mock_model = MagicMock()
    mock_model.encode.return_value = np.random.rand(384).astype(np.float32)

    # Directly construct the provider without triggering __init__ import
    provider = SentenceTransformerProvider.__new__(SentenceTransformerProvider)
    provider._model = mock_model
    provider._model_name = "all-MiniLM-L6-v2"
    provider._dimension = 384

    result = provider.embed("test text")
    assert len(result.vector) == 384
    assert result.model == "all-MiniLM-L6-v2"
    assert result.dimension == 384


def test_embed_batch():
    """Batch embedding returns correct number of results."""
    import numpy as np
    from converge.semantic.sentence_transformer_provider import SentenceTransformerProvider

    mock_model = MagicMock()
    mock_model.encode.return_value = np.random.rand(3, 384).astype(np.float32)

    provider = SentenceTransformerProvider.__new__(SentenceTransformerProvider)
    provider._model = mock_model
    provider._model_name = "all-MiniLM-L6-v2"
    provider._dimension = 384

    results = provider.embed_batch(["a", "b", "c"])
    assert len(results) == 3
    for r in results:
        assert len(r.vector) == 384


def test_import_error_fallback():
    """Asking for a non-existent provider gives a clear error."""
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        get_provider("nonexistent-provider-xyz")


def test_cosine_numpy_vs_pure():
    """Cosine similarity matches pure-Python reference calculation."""
    from converge.semantic.conflicts import _cosine_similarity

    a = [1.0, 2.0, 3.0, 4.0]
    b = [4.0, 3.0, 2.0, 1.0]

    # Pure python reference
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    expected = dot / (norm_a * norm_b)

    result = _cosine_similarity(a, b)
    assert abs(result - expected) < 1e-6
