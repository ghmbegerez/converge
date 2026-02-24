"""Decision dataset export for offline analysis and model retraining."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from converge import event_log
from converge.defaults import QUERY_LIMIT_UNBOUNDED
from converge.models import Event, EventType, now_iso


def export_decisions(
    output_path: str | Path | None = None,
    tenant_id: str | None = None,
    fmt: str = "jsonl",
) -> dict[str, Any]:
    """Export structured decision dataset for offline analysis and model retraining.

    Each record joins: intent → simulation → risk → policy → decision.
    Output: JSONL (one JSON object per line) or CSV.
    """
    intents = event_log.list_intents(tenant_id=tenant_id, limit=QUERY_LIMIT_UNBOUNDED)
    records = [_build_decision_record(intent) for intent in intents]

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

    event_log.append(Event(
        event_type=EventType.DATASET_EXPORTED,
        tenant_id=tenant_id,
        payload=result,
        evidence={"records": len(records)},
    ))

    return result


def _build_decision_record(intent: Any) -> dict[str, Any]:
    """Build a single flat decision record by joining intent/sim/risk/policy events."""
    risk_events = event_log.query(event_type=EventType.RISK_EVALUATED, intent_id=intent.id, limit=1)
    sim_events = event_log.query(event_type=EventType.SIMULATION_COMPLETED, intent_id=intent.id, limit=1)
    policy_events = event_log.query(event_type=EventType.POLICY_EVALUATED, intent_id=intent.id, limit=1)

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
