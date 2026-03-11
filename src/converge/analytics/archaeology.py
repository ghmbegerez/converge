"""Archaeology: git history analysis — hotspots, coupling, bus factor."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from converge import event_log, scm
from converge.defaults import QUERY_LIMIT_MEDIUM
from converge.models import now_iso

# --- Constants ---
_DEFAULT_MAX_COMMITS = 400
_ARCHAEOLOGY_TOP_N = 20
_BUS_FACTOR_THRESHOLD = 0.05
_HOTSPOT_CHANGE_THRESHOLD = 10
_COUPLING_MIN_CO_CHANGES = 2
_COUPLING_TOP_N = 50
_QUICK_COUPLING_MAX_COMMITS = 200

_SNAPSHOT_PATH = Path(".converge/archaeology_snapshot.json")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_snapshot() -> dict[str, Any] | None:
    """Load cached archaeology snapshot if it exists."""
    if _SNAPSHOT_PATH.exists():
        with open(_SNAPSHOT_PATH) as f:
            return json.load(f)
    return None


def _compute_coupling(entries: list[dict[str, Any]]) -> Counter[tuple[str, str]]:
    """Compute file co-change coupling from log entries."""
    coupling: Counter[tuple[str, str]] = Counter()
    for e in entries:
        files = sorted(set(e["files"]))
        for i, f1 in enumerate(files):
            for f2 in files[i + 1:]:
                coupling[(f1, f2)] += 1
    return coupling


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def archaeology_report(
    max_commits: int = _DEFAULT_MAX_COMMITS,
    top: int = _ARCHAEOLOGY_TOP_N,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Analyze git history for hotspots, coupling, and bus factor."""
    entries = scm.log_entries(max_commits=max_commits, cwd=cwd)
    if not entries:
        return {"error": "No git history available", "commits_analyzed": 0}

    # Hotspots: most frequently changed files
    file_changes: Counter[str] = Counter()
    for e in entries:
        for f in e["files"]:
            file_changes[f] += 1

    hotspots = [{"file": f, "changes": c} for f, c in file_changes.most_common(top)]

    # Coupling: files frequently changed together
    coupling = _compute_coupling(entries)
    top_coupling = [{"file_a": a, "file_b": b, "co_changes": c}
                    for (a, b), c in coupling.most_common(top)]

    # Author contribution
    author_commits: Counter[str] = Counter()
    author_files: dict[str, set[str]] = {}
    for e in entries:
        author_commits[e["author"]] += 1
        if e["author"] not in author_files:
            author_files[e["author"]] = set()
        author_files[e["author"]].update(e["files"])

    authors = [{"author": a, "commits": c, "files_touched": len(author_files.get(a, set()))}
               for a, c in author_commits.most_common(top)]

    # Bus factor: how many authors contribute significantly
    total_commits = len(entries)
    significant_authors = sum(1 for c in author_commits.values() if c >= total_commits * _BUS_FACTOR_THRESHOLD)
    bus_factor = max(1, significant_authors)

    return {
        "commits_analyzed": len(entries),
        "hotspots": hotspots,
        "coupling": top_coupling,
        "authors": authors,
        "bus_factor": bus_factor,
        "timestamp": now_iso(),
    }


def load_coupling_data(
    cwd: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load coupling data for risk scoring integration.

    Strategy (AR-07):
      1. Cached snapshot -> source="snapshot"
      2. Enrich with link-based coupling -> source="hybrid"
      3. Fallback: git log on-the-fly -> source="git-log"
    """
    snapshot = _load_snapshot()
    if snapshot is not None:
        coupling = snapshot.get("coupling", [])
        freshness = snapshot.get("timestamp", "")
        for item in coupling:
            item.setdefault("source", "snapshot")
            item.setdefault("freshness", freshness)

        if event_log.get_store() is not None:
            link_coupling = _coupling_from_links()
            if link_coupling:
                coupling = _merge_coupling(coupling, link_coupling, source="hybrid")
        return coupling

    entries = scm.log_entries(max_commits=_QUICK_COUPLING_MAX_COMMITS, cwd=cwd)
    freshness = now_iso()
    if entries:
        raw = _compute_coupling(entries)
        coupling = [{"file_a": a, "file_b": b, "co_changes": c, "source": "git-log", "freshness": freshness}
                    for (a, b), c in raw.most_common(_COUPLING_TOP_N) if c >= _COUPLING_MIN_CO_CHANGES]
    else:
        coupling = []

    if event_log.get_store() is not None:
        link_coupling = _coupling_from_links()
        if link_coupling:
            coupling = _merge_coupling(coupling, link_coupling, source="hybrid") if coupling else link_coupling

    return coupling


def _coupling_from_links() -> list[dict[str, Any]]:
    """Derive coupling from intent commit links (AR-07)."""
    intents = event_log.list_intents(limit=QUERY_LIMIT_MEDIUM)
    intent_files: dict[str, list[str]] = {}
    for intent in intents:
        links = event_log.list_commit_links(intent.id)
        if links:
            scope = intent.technical.get("scope_hint", [])
            if scope:
                intent_files[intent.id] = scope

    if not intent_files:
        return []

    coupling: Counter[tuple[str, str]] = Counter()
    for files in intent_files.values():
        sorted_files = sorted(set(files))
        for i, f1 in enumerate(sorted_files):
            for f2 in sorted_files[i + 1:]:
                coupling[(f1, f2)] += 1

    freshness = now_iso()
    return [{"file_a": a, "file_b": b, "co_changes": c, "source": "linked-history", "freshness": freshness}
            for (a, b), c in coupling.most_common(_COUPLING_TOP_N) if c >= _COUPLING_MIN_CO_CHANGES]


def _merge_coupling(
    base: list[dict[str, Any]],
    extra: list[dict[str, Any]],
    source: str = "hybrid",
) -> list[dict[str, Any]]:
    """Merge two coupling lists, summing co_changes for overlapping pairs."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in base:
        key = (item["file_a"], item["file_b"])
        index[key] = dict(item)

    for item in extra:
        key = (item["file_a"], item["file_b"])
        if key in index:
            index[key]["co_changes"] += item["co_changes"]
            index[key]["source"] = source
        else:
            index[key] = dict(item)

    result = sorted(index.values(), key=lambda x: x["co_changes"], reverse=True)
    return result[:_COUPLING_TOP_N]


def load_hotspot_set(cwd: str | Path | None = None) -> set[str]:
    """Load hotspot files (high churn) for risk enrichment."""
    snapshot = _load_snapshot()
    if snapshot is not None:
        return {h["file"] for h in snapshot.get("hotspots", []) if h.get("changes", 0) >= _HOTSPOT_CHANGE_THRESHOLD}

    entries = scm.log_entries(max_commits=_QUICK_COUPLING_MAX_COMMITS, cwd=cwd)
    if not entries:
        return set()

    file_changes: Counter[str] = Counter()
    for e in entries:
        for f in e["files"]:
            file_changes[f] += 1

    return {f for f, c in file_changes.items() if c >= _HOTSPOT_CHANGE_THRESHOLD}


def save_archaeology_snapshot(
    report: dict[str, Any],
    output_path: str | Path | None = None,
) -> str:
    """Save archaeology report to JSON file."""
    path = Path(output_path or ".converge/archaeology_snapshot.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    return str(path)


# ---------------------------------------------------------------------------
# Snapshot refresh and validation (AR-09)
# ---------------------------------------------------------------------------

def refresh_snapshot(
    max_commits: int = _DEFAULT_MAX_COMMITS,
    cwd: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Regenerate archaeology snapshot and validate key counters."""
    report = archaeology_report(max_commits=max_commits, cwd=cwd)
    if "error" in report:
        return {"valid": False, "error": report["error"]}

    path = save_archaeology_snapshot(report, output_path)

    validation = _validate_snapshot(report)
    return {
        "valid": validation["valid"],
        "path": path,
        "commits_analyzed": report["commits_analyzed"],
        "hotspot_count": len(report.get("hotspots", [])),
        "coupling_count": len(report.get("coupling", [])),
        "author_count": len(report.get("authors", [])),
        "bus_factor": report.get("bus_factor", 0),
        "issues": validation.get("issues", []),
        "timestamp": report.get("timestamp", now_iso()),
    }


def _validate_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    """Validate snapshot integrity: non-empty, consistent counters."""
    issues: list[str] = []

    if report.get("commits_analyzed", 0) == 0:
        issues.append("Zero commits analyzed")

    hotspots = report.get("hotspots", [])
    authors = report.get("authors", [])

    if not hotspots:
        issues.append("No hotspots found")
    if not authors:
        issues.append("No authors found")

    bus_factor = report.get("bus_factor", 0)
    if bus_factor == 0:
        issues.append("Bus factor is zero")

    return {"valid": len(issues) == 0, "issues": issues}
