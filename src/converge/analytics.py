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
_CALIBRATION_QUERY_LIMIT = 10000
_EXPORT_INTENT_LIMIT = 100000
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


def load_coupling_data(cwd: str | Path | None = None) -> list[dict[str, Any]]:
    """Load coupling data for risk scoring integration.

    Tries cached archaeology snapshot first, then computes on-the-fly.
    Returns list of {file_a, file_b, co_changes} suitable for risk.evaluate_risk().
    """
    snapshot = _load_snapshot()
    if snapshot is not None:
        return snapshot.get("coupling", [])

    # No cache — compute a quick coupling from recent commits
    entries = scm.log_entries(max_commits=_QUICK_COUPLING_MAX_COMMITS, cwd=cwd)
    if not entries:
        return []

    coupling = _compute_coupling(entries)
    return [{"file_a": a, "file_b": b, "co_changes": c}
            for (a, b), c in coupling.most_common(_COUPLING_TOP_N) if c >= _COUPLING_MIN_CO_CHANGES]


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
# Calibration (data-driven threshold adjustment)
# ---------------------------------------------------------------------------

def run_calibration(
    db_path: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Calibrate policy profiles from historical risk data."""
    # Gather historical risk scores from events
    risk_events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, limit=_CALIBRATION_QUERY_LIMIT)
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

    event_log.append(db_path, Event(
        event_type=EventType.CALIBRATION_COMPLETED,
        payload=result,
        evidence={"data_points": len(historical)},
    ))

    return result


# ---------------------------------------------------------------------------
# Risk review (comprehensive per-intent report)
# ---------------------------------------------------------------------------

def risk_review(
    db_path: str | Path,
    intent_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build comprehensive risk review for an intent."""
    from converge import projections

    intent = event_log.get_intent(db_path, intent_id)
    if intent is None:
        return {"error": f"Intent {intent_id} not found"}

    events = _gather_intent_events(db_path, intent_id)
    compliance = projections.compliance_report(db_path, tenant_id=tenant_id)
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


def _gather_intent_events(db_path: str | Path, intent_id: str) -> dict[str, Any]:
    """Gather latest risk/sim/policy/decision events for an intent."""
    risk_events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, intent_id=intent_id, limit=1)
    sim_events = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, intent_id=intent_id, limit=1)
    policy_events = event_log.query(db_path, event_type=EventType.POLICY_EVALUATED, intent_id=intent_id, limit=1)
    decisions = event_log.query(db_path, intent_id=intent_id, limit=_DECISION_QUERY_LIMIT)
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


# ---------------------------------------------------------------------------
# Decision dataset export
# ---------------------------------------------------------------------------

def export_decisions(
    db_path: str | Path,
    output_path: str | Path | None = None,
    tenant_id: str | None = None,
    fmt: str = "jsonl",
) -> dict[str, Any]:
    """Export structured decision dataset for offline analysis and model retraining.

    Each record joins: intent → simulation → risk → policy → decision.
    Output: JSONL (one JSON object per line) or CSV.
    """
    intents = event_log.list_intents(db_path, tenant_id=tenant_id, limit=_EXPORT_INTENT_LIMIT)
    records = [_build_decision_record(db_path, intent) for intent in intents]

    path = Path(output_path or f".converge/datasets/decisions.{fmt}")
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        _write_csv(records, path)
    else:
        _write_jsonl(records, path)

    result = {
        "records": len(records),
        "format": fmt,
        "output_path": str(path),
        "timestamp": now_iso(),
    }

    event_log.append(db_path, Event(
        event_type=EventType.DATASET_EXPORTED,
        tenant_id=tenant_id,
        payload=result,
        evidence={"records": len(records)},
    ))

    return result


def _build_decision_record(db_path: str | Path, intent: Any) -> dict[str, Any]:
    """Build a single flat decision record by joining intent/sim/risk/policy events."""
    risk_events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, intent_id=intent.id, limit=1)
    sim_events = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, intent_id=intent.id, limit=1)
    policy_events = event_log.query(db_path, event_type=EventType.POLICY_EVALUATED, intent_id=intent.id, limit=1)

    risk_data = risk_events[0]["payload"] if risk_events else {}
    sim_data = sim_events[0]["payload"] if sim_events else {}
    policy_data = policy_events[0]["payload"] if policy_events else {}
    signals = risk_data.get("signals", {})

    return {
        "intent_id": intent.id,
        "source": intent.source,
        "target": intent.target,
        "status": intent.status.value,
        "risk_level": intent.risk_level.value,
        "priority": intent.priority,
        "retries": intent.retries,
        "tenant_id": intent.tenant_id,
        "created_at": intent.created_at,
        # Simulation
        "mergeable": sim_data.get("mergeable"),
        "conflict_count": len(sim_data.get("conflicts", [])),
        "files_changed_count": len(sim_data.get("files_changed", [])),
        # Risk scores
        "risk_score": risk_data.get("risk_score"),
        "damage_score": risk_data.get("damage_score"),
        "entropy_score": risk_data.get("entropy_score"),
        "propagation_score": risk_data.get("propagation_score"),
        "containment_score": risk_data.get("containment_score"),
        # 4 signals
        "entropic_load": signals.get("entropic_load"),
        "contextual_value": signals.get("contextual_value"),
        "complexity_delta": signals.get("complexity_delta"),
        "path_dependence": signals.get("path_dependence"),
        # Bombs
        "bomb_count": len(risk_data.get("bombs", [])),
        "bomb_types": [b.get("type") for b in risk_data.get("bombs", [])],
        # Policy
        "policy_verdict": policy_data.get("verdict"),
        "policy_profile": policy_data.get("profile_used"),
        # Graph
        "graph_nodes": risk_data.get("graph_metrics", {}).get("nodes"),
        "graph_edges": risk_data.get("graph_metrics", {}).get("edges"),
        "graph_density": risk_data.get("graph_metrics", {}).get("density"),
    }


def _write_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    """Write records as JSONL (one JSON object per line)."""
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")


def _write_csv(records: list[dict[str, Any]], path: Path) -> None:
    """Write records as CSV with flattened list columns."""
    import csv
    if not records:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        for r in records:
            r["bomb_types"] = ",".join(r.get("bomb_types") or [])
            writer.writerow(r)
