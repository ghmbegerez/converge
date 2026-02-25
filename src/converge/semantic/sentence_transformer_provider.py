"""Real semantic embeddings via sentence-transformers.

Provides actual semantic similarity (cosine distance reflects meaning)
instead of the deterministic hash-based provider used in CI.

Requires: pip install sentence-transformers  (or: pip install converge[semantic])
"""

from __future__ import annotations

from converge.semantic.embeddings import EmbeddingProvider, EmbeddingResult


class SentenceTransformerProvider(EmbeddingProvider):
    """Real semantic embeddings via sentence-transformers."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", dimension: int = 384):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self._model_name = model_name
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> EmbeddingResult:
        vector = self._model.encode(text).tolist()
        return EmbeddingResult(
            vector=vector,
            model=self._model_name,
            dimension=self._dimension,
        )

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        vectors = self._model.encode(texts)
        return [
            EmbeddingResult(
                vector=v.tolist(),
                model=self._model_name,
                dimension=self._dimension,
            )
            for v in vectors
        ]
