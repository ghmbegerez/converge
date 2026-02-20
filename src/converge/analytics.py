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


# ---------------------------------------------------------------------------
# Archaeology (git history analysis)
# ---------------------------------------------------------------------------

def archaeology_report(
    max_commits: int = 400,
    top: int = 20,
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
    coupling: Counter[tuple[str, str]] = Counter()
    for e in entries:
        files = sorted(set(e["files"]))
        for i, f1 in enumerate(files):
            for f2 in files[i + 1:]:
                coupling[(f1, f2)] += 1

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
    significant_authors = sum(1 for c in author_commits.values() if c >= total_commits * 0.05)
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
    snapshot_path = Path(".converge/archaeology_snapshot.json")
    if snapshot_path.exists():
        with open(snapshot_path) as f:
            data = json.load(f)
        return data.get("coupling", [])

    # No cache — compute a quick coupling from recent commits
    entries = scm.log_entries(max_commits=200, cwd=cwd)
    if not entries:
        return []

    coupling: Counter[tuple[str, str]] = Counter()
    for e in entries:
        files = sorted(set(e["files"]))
        for i, f1 in enumerate(files):
            for f2 in files[i + 1:]:
                coupling[(f1, f2)] += 1

    # Only return pairs with 2+ co-changes (meaningful coupling)
    return [{"file_a": a, "file_b": b, "co_changes": c}
            for (a, b), c in coupling.most_common(50) if c >= 2]


def load_hotspot_set(cwd: str | Path | None = None) -> set[str]:
    """Load hotspot files (high churn) for risk enrichment."""
    snapshot_path = Path(".converge/archaeology_snapshot.json")
    if snapshot_path.exists():
        with open(snapshot_path) as f:
            data = json.load(f)
        # Files with 10+ changes are hotspots
        return {h["file"] for h in data.get("hotspots", []) if h.get("changes", 0) >= 10}

    entries = scm.log_entries(max_commits=200, cwd=cwd)
    if not entries:
        return set()

    file_changes: Counter[str] = Counter()
    for e in entries:
        for f in e["files"]:
            file_changes[f] += 1

    return {f for f, c in file_changes.items() if c >= 10}


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
    risk_events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, limit=10000)
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
    from converge import projections, risk as risk_mod

    intent = event_log.get_intent(db_path, intent_id)
    if intent is None:
        return {"error": f"Intent {intent_id} not found"}

    # Gather all events for this intent
    risk_events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, intent_id=intent_id, limit=1)
    sim_events = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, intent_id=intent_id, limit=1)
    policy_events = event_log.query(db_path, event_type=EventType.POLICY_EVALUATED, intent_id=intent_id, limit=1)
    decision_events = event_log.query(db_path, intent_id=intent_id, limit=50)

    # Compliance snapshot
    compliance = projections.compliance_report(db_path, tenant_id=tenant_id)

    # Build diagnostics if we have risk data
    diagnostics = []
    if risk_events and sim_events:
        from converge.models import RiskEval, Simulation
        re = risk_events[0]["payload"]
        risk_eval = RiskEval(
            intent_id=intent_id,
            risk_score=re.get("risk_score", 0),
            damage_score=re.get("damage_score", 0),
            entropy_score=re.get("entropy_score", 0),
            propagation_score=re.get("propagation_score", 0),
            containment_score=re.get("containment_score", 0),
            findings=re.get("findings", []),
            impact_edges=re.get("impact_edges", []),
        )
        sp = sim_events[0]["payload"]
        sim = Simulation(
            mergeable=sp.get("mergeable", True),
            conflicts=sp.get("conflicts", []),
            files_changed=sp.get("files_changed", []),
        )
        diagnostics = risk_mod.build_diagnostics(intent, risk_eval, sim)

    review = {
        "intent_id": intent_id,
        "intent": intent.to_dict(),
        "risk": risk_events[0]["payload"] if risk_events else None,
        "simulation": sim_events[0]["payload"] if sim_events else None,
        "policy": policy_events[0]["payload"] if policy_events else None,
        "diagnostics": diagnostics,
        "compliance": compliance.to_dict(),
        "decision_history": [{"event_type": e["event_type"], "timestamp": e["timestamp"],
                               "payload": e["payload"]} for e in decision_events],
        "timestamp": now_iso(),
        "tenant_id": tenant_id,
    }

    # Add learning
    if risk_events:
        re = risk_events[0]["payload"]
        review["learning"] = _derive_review_learning(re, diagnostics, compliance)

    return review


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
                      "; ".join(d.get("explanation", "") for d in critical_diags[:3]),
            "priority": 0,
        })
    risk_score = risk_data.get("risk_score", 0)
    if risk_score > 50:
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
    intents = event_log.list_intents(db_path, tenant_id=tenant_id, limit=100000)
    records = []

    for intent in intents:
        # Gather latest events for this intent
        risk_events = event_log.query(db_path, event_type=EventType.RISK_EVALUATED, intent_id=intent.id, limit=1)
        sim_events = event_log.query(db_path, event_type=EventType.SIMULATION_COMPLETED, intent_id=intent.id, limit=1)
        policy_events = event_log.query(db_path, event_type=EventType.POLICY_EVALUATED, intent_id=intent.id, limit=1)

        risk_data = risk_events[0]["payload"] if risk_events else {}
        sim_data = sim_events[0]["payload"] if sim_events else {}
        policy_data = policy_events[0]["payload"] if policy_events else {}
        signals = risk_data.get("signals", {})

        record = {
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
        records.append(record)

    # Write output
    path = Path(output_path or f".converge/datasets/decisions.{fmt}")
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        import csv
        if records:
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=records[0].keys())
                writer.writeheader()
                for r in records:
                    # Flatten lists to strings for CSV
                    r["bomb_types"] = ",".join(r.get("bomb_types") or [])
                    writer.writerow(r)
    else:
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r, default=str) + "\n")

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
