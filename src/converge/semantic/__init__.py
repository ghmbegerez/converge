"""Semantic processing: canonical text, embeddings, conflict detection."""

from converge.semantic.canonical import build_canonical_text, canonical_checksum
from converge.semantic.conflicts import list_conflicts, resolve_conflict, scan_conflicts
from converge.semantic.embeddings import EmbeddingProvider, get_provider

__all__ = [
    "build_canonical_text",
    "canonical_checksum",
    "EmbeddingProvider",
    "get_provider",
    "scan_conflicts",
    "list_conflicts",
    "resolve_conflict",
]
