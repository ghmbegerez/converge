"""Tests for decision dataset export module."""

import json
from pathlib import Path

from conftest import make_intent

from converge import event_log, exports
from converge.models import Event, EventType, RiskLevel, Simulation, Status


def _emit_sim_and_risk(intent_id: str, tenant_id: str = "team-a") -> None:
    """Emit simulation, risk, and policy events for an intent."""
    event_log.append(Event(
        event_type=EventType.SIMULATION_COMPLETED,
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload={
            "mergeable": True,
            "conflicts": [],
            "files_changed": ["a.py"],
            "source": "feature/x",
            "target": "main",
        },
    ))
    event_log.append(Event(
        event_type=EventType.RISK_EVALUATED,
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload={
            "risk_score": 25.0,
            "damage_score": 10.0,
            "entropy_score": 5.0,
            "propagation_score": 8.0,
            "containment_score": 90.0,
            "signals": {
                "entropic_load": 3.0,
                "contextual_value": 7.0,
                "complexity_delta": 2.0,
                "path_dependence": 1.5,
            },
            "bombs": [],
            "graph_metrics": {"nodes": 5, "edges": 4, "density": 0.4},
        },
    ))
    event_log.append(Event(
        event_type=EventType.POLICY_EVALUATED,
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload={"verdict": "ALLOW", "profile_used": "medium"},
    ))


class TestExportJSONL:
    def test_export_jsonl_creates_file(self, db_path, tmp_path):
        """JSONL export creates a file."""
        make_intent("exp-001")
        _emit_sim_and_risk("exp-001")

        output = tmp_path / "decisions.jsonl"
        result = exports.export_decisions(output_path=output, fmt="jsonl")
        assert result["records"] == 1
        assert output.exists()
        lines = output.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_export_record_structure(self, db_path, tmp_path):
        """Exported records have expected fields."""
        make_intent("exp-002")
        _emit_sim_and_risk("exp-002")

        output = tmp_path / "decisions.jsonl"
        exports.export_decisions(output_path=output, fmt="jsonl")

        record = json.loads(output.read_text().strip().splitlines()[0])
        expected_fields = [
            "intent_id", "source", "target", "status", "risk_level",
            "mergeable", "risk_score", "damage_score", "policy_verdict",
        ]
        for field in expected_fields:
            assert field in record, f"Missing field: {field}"


class TestExportCSV:
    def test_export_csv_creates_file(self, db_path, tmp_path):
        """CSV export creates a file with headers."""
        make_intent("exp-003")
        _emit_sim_and_risk("exp-003")

        output = tmp_path / "decisions.csv"
        result = exports.export_decisions(output_path=output, fmt="csv")
        assert result["records"] == 1
        assert output.exists()

        lines = output.read_text().strip().splitlines()
        assert len(lines) == 2  # header + 1 row
        assert "intent_id" in lines[0]


class TestExportEdgeCases:
    def test_export_empty_db(self, db_path, tmp_path):
        """Empty DB produces 0 records."""
        output = tmp_path / "empty.jsonl"
        result = exports.export_decisions(output_path=output, fmt="jsonl")
        assert result["records"] == 0

    def test_export_tenant_filter(self, db_path, tmp_path):
        """Only records for the requested tenant are exported."""
        make_intent("exp-010", tenant_id="team-a")
        make_intent("exp-011", tenant_id="team-b")
        _emit_sim_and_risk("exp-010", tenant_id="team-a")
        _emit_sim_and_risk("exp-011", tenant_id="team-b")

        output = tmp_path / "filtered.jsonl"
        result = exports.export_decisions(
            output_path=output, tenant_id="team-a", fmt="jsonl",
        )
        assert result["records"] == 1

        record = json.loads(output.read_text().strip().splitlines()[0])
        assert record["intent_id"] == "exp-010"


class TestExportEvents:
    def test_export_emits_event(self, db_path, tmp_path):
        """DATASET_EXPORTED event is emitted."""
        make_intent("exp-020")
        _emit_sim_and_risk("exp-020")

        output = tmp_path / "decisions.jsonl"
        exports.export_decisions(output_path=output, fmt="jsonl")

        events = event_log.query(event_type=EventType.DATASET_EXPORTED)
        assert len(events) >= 1
        assert events[0]["payload"]["records"] == 1

    def test_export_custom_path(self, db_path, tmp_path):
        """Output is written to the custom path."""
        make_intent("exp-030")
        _emit_sim_and_risk("exp-030")

        custom = tmp_path / "custom" / "output.jsonl"
        result = exports.export_decisions(output_path=custom, fmt="jsonl")
        assert result["output_path"] == str(custom)
        assert custom.exists()
