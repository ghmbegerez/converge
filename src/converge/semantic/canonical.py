"""Deterministic canonical text builder for intent semantic fingerprinting.

Produces a stable text representation from intent metadata, commit links,
and coupling context.  Same input always produces the same output (and checksum).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def build_canonical_text(
    intent: Any,
    *,
    commit_links: list[dict[str, Any]] | None = None,
    coupling: list[dict[str, Any]] | None = None,
) -> str:
    """Build deterministic canonical text from an intent and its context.

    Sections are emitted in fixed order.  Within each section, keys are sorted.
    Missing or empty fields produce empty sections (omitted from output).
    """
    parts: list[str] = []

    # Section 1: identity
    parts.append(f"intent:{intent.id}")
    parts.append(f"source:{intent.source}")
    parts.append(f"target:{intent.target}")
    parts.append(f"risk:{intent.risk_level.value}")

    if intent.plan_id:
        parts.append(f"plan:{intent.plan_id}")

    # Section 2: semantic metadata (sorted keys for determinism)
    sem = intent.semantic or {}
    for key in sorted(sem.keys()):
        val = sem[key]
        if val:
            parts.append(f"semantic.{key}:{val}")

    # Section 3: scope hint from technical (sorted)
    tech = intent.technical or {}
    scope = tech.get("scope_hint", [])
    if scope:
        for s in sorted(scope):
            parts.append(f"scope:{s}")

    # Section 4: dependencies (sorted)
    if intent.dependencies:
        for dep in sorted(intent.dependencies):
            parts.append(f"dep:{dep}")

    # Section 5: commit links (sorted by sha+role for determinism)
    if commit_links:
        for link in sorted(commit_links, key=lambda l: (l.get("sha", ""), l.get("role", ""))):
            parts.append(f"link:{link.get('sha', '')}:{link.get('role', '')}")

    # Section 6: coupling context (sorted by file pair)
    if coupling:
        for c in sorted(coupling, key=lambda x: (x.get("file_a", ""), x.get("file_b", ""))):
            parts.append(f"coupling:{c.get('file_a', '')}:{c.get('file_b', '')}:{c.get('co_changes', 0)}")

    return "\n".join(parts)


def canonical_checksum(canonical_text: str) -> str:
    """Return SHA-256 hex digest of the canonical text."""
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
