"""Analytics: archaeology (git history) and calibration (threshold adjustment).

These are on-demand analytical capabilities that operate on larger datasets
(git log, event history) to provide insights and optimize policy thresholds.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from converge import event_log, scm
from converge.defaults import QUERY_LIMIT_LARGE, QUERY_LIMIT_MEDIUM
from converge.models import Event, EventType, now_iso
from converge.policy import calibrate_profiles, load_config

# --- Archaeology constants ---
_DEFAULT_MAX_COMMITS = 400
_ARCHAEOLOGY_TOP_N = 20
_BUS_FACTOR_THRESHOLD = 0.05
_HOTSPOT_CHANGE_THRESHOLD = 10
_COUPLING_MIN_CO_CHANGES = 2
_COUPLING_TOP_N = 50
_QUICK_COUPLING_MAX_COMMITS = 200

# --- Review constants ---
_REVIEW_RISK_THRESHOLD = 50
_REVIEW_CRITICAL_DISPLAY = 3

# --- Query/export limits ---
_DECISION_QUERY_LIMIT = 50


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
# Archaeology (git history analysis)
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
      1. Cached snapshot → source="snapshot"
      2. Enrich with link-based coupling → source="hybrid"
      3. Fallback: git log on-the-fly → source="git-log"

    Returns list of {file_a, file_b, co_changes, source, freshness} suitable
    for risk.evaluate_risk().
    """
    snapshot = _load_snapshot()
    if snapshot is not None:
        coupling = snapshot.get("coupling", [])
        freshness = snapshot.get("timestamp", "")
        # AR-08: annotate provenance
        for item in coupling:
            item.setdefault("source", "snapshot")
            item.setdefault("freshness", freshness)

        # AR-07: enrich with link-based coupling if store is available
        if event_log.get_store() is not None:
            link_coupling = _coupling_from_links()
            if link_coupling:
                coupling = _merge_coupling(coupling, link_coupling, source="hybrid")
        return coupling

    # No cache — compute a quick coupling from recent commits
    entries = scm.log_entries(max_commits=_QUICK_COUPLING_MAX_COMMITS, cwd=cwd)
    freshness = now_iso()
    if entries:
        raw = _compute_coupling(entries)
        coupling = [{"file_a": a, "file_b": b, "co_changes": c, "source": "git-log", "freshness": freshness}
                    for (a, b), c in raw.most_common(_COUPLING_TOP_N) if c >= _COUPLING_MIN_CO_CHANGES]
    else:
        coupling = []

    # AR-07: enrich with link-based coupling if store is available
    if event_log.get_store() is not None:
        link_coupling = _coupling_from_links()
        if link_coupling:
            coupling = _merge_coupling(coupling, link_coupling, source="hybrid") if coupling else link_coupling

    return coupling


def _coupling_from_links() -> list[dict[str, Any]]:
    """Derive coupling from intent commit links (AR-07).

    Intents that share commits or touch overlapping files imply coupling
    between those files. This is a lightweight heuristic based on link data.
    """
    intents = event_log.list_intents(limit=QUERY_LIMIT_MEDIUM)
    # Group files by intent from technical.scope_hint
    intent_files: dict[str, list[str]] = {}
    for intent in intents:
        links = event_log.list_commit_links(intent.id)
        if links:
            scope = intent.technical.get("scope_hint", [])
            if scope:
                intent_files[intent.id] = scope

    if not intent_files:
        return []

    # Compute co-change from scope hints across linked intents
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
    """Regenerate archaeology snapshot and validate key counters.

    Returns validation result with pass/fail status.
    """
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
    coupling = report.get("coupling", [])
    authors = report.get("authors", [])

    if not hotspots:
        issues.append("No hotspots found")
    if not authors:
        issues.append("No authors found")

    # Coupling can legitimately be empty for small repos
    bus_factor = report.get("bus_factor", 0)
    if bus_factor == 0:
        issues.append("Bus factor is zero")

    return {"valid": len(issues) == 0, "issues": issues}


# ---------------------------------------------------------------------------
# Calibration (data-driven threshold adjustment)
# ---------------------------------------------------------------------------

def run_calibration(
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Calibrate policy profiles from historical risk data."""
    # Gather historical risk scores from events
    risk_events = event_log.query(event_type=EventType.RISK_EVALUATED, limit=QUERY_LIMIT_LARGE)
    historical = [e["payload"] for e in risk_events]

    config = load_config()
    new_profiles = calibrate_profiles(historical, config.profiles)

    result = {
        "calibrated_profiles": new_profiles,
        "data_points": len(historical),
        "timestamp": now_iso(),
    }

    # Save to file
    path = Path(output_path or ".converge/calibrated_profiles.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(new_profiles, f, indent=2)
    result["output_path"] = str(path)

    event_log.append(Event(
        event_type=EventType.CALIBRATION_COMPLETED,
        payload=result,
        evidence={"data_points": len(historical)},
    ))

    return result


# ---------------------------------------------------------------------------
# Risk review (comprehensive per-intent report)
# ---------------------------------------------------------------------------

def risk_review(
    intent_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build comprehensive risk review for an intent."""
    from converge import projections

    intent = event_log.get_intent(intent_id)
    if intent is None:
        return {"error": f"Intent {intent_id} not found"}

    events = _gather_intent_events(intent_id)
    compliance = projections.compliance_report(tenant_id=tenant_id)
    diagnostics = _build_review_diagnostics(intent, events)

    review = {
        "intent_id": intent_id,
        "intent": intent.to_dict(),
        "risk": events["risk_payload"],
        "simulation": events["sim_payload"],
        "policy": events["policy_payload"],
        "diagnostics": diagnostics,
        "compliance": compliance.to_dict(),
        "decision_history": [{"event_type": e["event_type"], "timestamp": e["timestamp"],
                               "payload": e["payload"]} for e in events["decisions"]],
        "timestamp": now_iso(),
        "tenant_id": tenant_id,
    }

    if events["risk_payload"]:
        review["learning"] = _derive_review_learning(events["risk_payload"], diagnostics, compliance)

    return review


def _gather_intent_events(intent_id: str) -> dict[str, Any]:
    """Gather latest risk/sim/policy/decision events for an intent."""
    risk_events = event_log.query(event_type=EventType.RISK_EVALUATED, intent_id=intent_id, limit=1)
    sim_events = event_log.query(event_type=EventType.SIMULATION_COMPLETED, intent_id=intent_id, limit=1)
    policy_events = event_log.query(event_type=EventType.POLICY_EVALUATED, intent_id=intent_id, limit=1)
    decisions = event_log.query(intent_id=intent_id, limit=_DECISION_QUERY_LIMIT)
    return {
        "risk_payload": risk_events[0]["payload"] if risk_events else None,
        "sim_payload": sim_events[0]["payload"] if sim_events else None,
        "policy_payload": policy_events[0]["payload"] if policy_events else None,
        "decisions": decisions,
    }


def _build_review_diagnostics(intent: Any, events: dict[str, Any]) -> list[dict[str, Any]]:
    """Build diagnostics from risk + simulation event payloads."""
    if not events["risk_payload"] or not events["sim_payload"]:
        return []

    from converge import risk as risk_mod
    from converge.models import RiskEval, Simulation

    re = events["risk_payload"]
    risk_eval = RiskEval(
        intent_id=intent.id,
        risk_score=re.get("risk_score", 0),
        damage_score=re.get("damage_score", 0),
        entropy_score=re.get("entropy_score", 0),
        propagation_score=re.get("propagation_score", 0),
        containment_score=re.get("containment_score", 0),
        findings=re.get("findings", []),
        impact_edges=re.get("impact_edges", []),
    )
    sp = events["sim_payload"]
    sim = Simulation(
        mergeable=sp.get("mergeable", True),
        conflicts=sp.get("conflicts", []),
        files_changed=sp.get("files_changed", []),
    )
    return risk_mod.build_diagnostics(intent, risk_eval, sim)


def _derive_review_learning(
    risk_data: dict,
    diagnostics: list[dict],
    compliance: Any,
) -> dict[str, Any]:
    lessons = []
    critical_diags = [d for d in diagnostics if d.get("severity") == "critical"]
    if critical_diags:
        lessons.append({
            "code": "learn.critical_diagnostics",
            "title": "Critical issues detected",
            "why": f"{len(critical_diags)} critical diagnostic(s) found",
            "action": "Address critical issues before proceeding: " +
                      "; ".join(d.get("explanation", "") for d in critical_diags[:_REVIEW_CRITICAL_DISPLAY]),
            "priority": 0,
        })
    risk_score = risk_data.get("risk_score", 0)
    if risk_score > _REVIEW_RISK_THRESHOLD:
        lessons.append({
            "code": "learn.review_risk",
            "title": "Elevated risk",
            "why": f"Risk score {risk_score:.0f}",
            "action": "Review impact graph and consider narrowing scope",
            "priority": 1,
        })
    return {"lessons": lessons, "summary": f"Review: {len(lessons)} actionable lesson(s)"}


