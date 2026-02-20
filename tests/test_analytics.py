"""Tests for analytics (archaeology, calibration, export, coupling)."""

import json

from converge import analytics, event_log
from converge.models import Event, Intent, RiskLevel, Status, now_iso


def _seed_full_pipeline(db_path, n=5):
    """Seed intent + simulation + risk + policy events for export tests."""
    for i in range(n):
        intent = Intent(
            id=f"exp-{i:03d}",
            source=f"feature/{i}",
            target="main",
            status=Status.MERGED,
            risk_level=RiskLevel.MEDIUM,
            priority=2,
            tenant_id="team-a",
        )
        event_log.upsert_intent(db_path, intent)

        event_log.append(db_path, Event(
            event_type="simulation.completed",
            intent_id=intent.id,
            tenant_id="team-a",
            payload={"mergeable": True, "conflicts": [], "files_changed": [f"f{i}.py"],
                     "source": f"feature/{i}", "target": "main"},
        ))
        event_log.append(db_path, Event(
            event_type="risk.evaluated",
            intent_id=intent.id,
            tenant_id="team-a",
            payload={
                "risk_score": 10.0 + i * 5,
                "damage_score": 5.0 + i * 2,
                "entropy_score": 3.0 + i,
                "propagation_score": 2.0 + i,
                "containment_score": 0.9 - i * 0.05,
                "signals": {
                    "entropic_load": 0.3 + i * 0.1,
                    "contextual_value": 0.2,
                    "complexity_delta": 0.1 + i * 0.05,
                    "path_dependence": 0.1,
                },
                "bombs": [],
            },
        ))
        event_log.append(db_path, Event(
            event_type="policy.evaluated",
            intent_id=intent.id,
            tenant_id="team-a",
            payload={"verdict": "ALLOW", "profile_used": "medium"},
        ))


class TestExportDecisions:
    def test_export_jsonl(self, db_path, tmp_path):
        _seed_full_pipeline(db_path)
        output = tmp_path / "decisions.jsonl"
        result = analytics.export_decisions(db_path, output_path=str(output), fmt="jsonl")

        assert result["records"] == 5
        assert result["format"] == "jsonl"
        assert output.exists()

        # Verify JSONL content
        lines = output.read_text().strip().split("\n")
        assert len(lines) == 5
        record = json.loads(lines[0])
        assert "intent_id" in record
        assert "risk_score" in record
        assert "entropic_load" in record

    def test_export_csv(self, db_path, tmp_path):
        _seed_full_pipeline(db_path)
        output = tmp_path / "decisions.csv"
        result = analytics.export_decisions(db_path, output_path=str(output), fmt="csv")

        assert result["records"] == 5
        assert output.exists()
        lines = output.read_text().strip().split("\n")
        assert len(lines) == 6  # header + 5 records
        assert "intent_id" in lines[0]

    def test_export_records_event(self, db_path, tmp_path):
        _seed_full_pipeline(db_path)
        analytics.export_decisions(db_path, output_path=str(tmp_path / "d.jsonl"))
        events = event_log.query(db_path, event_type="dataset.exported")
        assert len(events) >= 1

    def test_export_empty(self, db_path, tmp_path):
        output = tmp_path / "empty.jsonl"
        result = analytics.export_decisions(db_path, output_path=str(output))
        assert result["records"] == 0


class TestCouplingData:
    def test_load_coupling_no_data(self, tmp_path):
        """Returns empty when no archaeology snapshot or git."""
        result = analytics.load_coupling_data(cwd=str(tmp_path))
        assert isinstance(result, list)
        assert len(result) == 0

    def test_load_coupling_from_snapshot(self, tmp_path):
        """Loads coupling from cached snapshot."""
        snapshot = {
            "coupling": [
                {"file_a": "a.py", "file_b": "b.py", "co_changes": 5},
                {"file_a": "c.py", "file_b": "d.py", "co_changes": 3},
            ]
        }
        snapshot_dir = tmp_path / ".converge"
        snapshot_dir.mkdir()
        (snapshot_dir / "archaeology_snapshot.json").write_text(json.dumps(snapshot))

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = analytics.load_coupling_data()
            assert len(result) == 2
            assert result[0]["file_a"] == "a.py"
        finally:
            os.chdir(old_cwd)


class TestHotspotSet:
    def test_load_hotspot_from_snapshot(self, tmp_path):
        """Loads hotspots from cached snapshot."""
        snapshot = {
            "hotspots": [
                {"file": "hot.py", "changes": 15},
                {"file": "cold.py", "changes": 3},
            ]
        }
        snapshot_dir = tmp_path / ".converge"
        snapshot_dir.mkdir()
        (snapshot_dir / "archaeology_snapshot.json").write_text(json.dumps(snapshot))

        import os
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = analytics.load_hotspot_set()
            assert "hot.py" in result
            assert "cold.py" not in result  # below threshold
        finally:
            os.chdir(old_cwd)


class TestRiskReview:
    """analytics.risk_review() builds a comprehensive per-intent report."""

    def test_review_nonexistent_intent(self, db_path):
        result = analytics.risk_review(db_path, "nonexistent")
        assert "error" in result

    def test_review_with_full_pipeline_data(self, db_path):
        """Review assembles risk, simulation, policy, diagnostics, and compliance."""
        _seed_full_pipeline(db_path, n=1)
        result = analytics.risk_review(db_path, "exp-000", tenant_id="team-a")

        assert result["intent_id"] == "exp-000"
        assert result["intent"] is not None
        assert result["intent"]["source"] == "feature/0"
        assert result["risk"] is not None
        assert result["risk"]["risk_score"] == 10.0
        assert result["simulation"] is not None
        assert result["simulation"]["mergeable"] is True
        assert result["policy"] is not None
        assert result["policy"]["verdict"] == "ALLOW"
        assert result["compliance"] is not None
        assert "decision_history" in result
        assert len(result["decision_history"]) > 0

    def test_review_includes_learning_when_risk_data_exists(self, db_path):
        """Review includes learning section with actionable lessons."""
        _seed_full_pipeline(db_path, n=1)
        result = analytics.risk_review(db_path, "exp-000")

        assert "learning" in result
        assert "lessons" in result["learning"]
        assert "summary" in result["learning"]

    def test_review_without_simulation_data(self, db_path):
        """Review works even when simulation events are missing."""
        intent = Intent(
            id="rev-no-sim",
            source="feature/test",
            target="main",
            status=Status.READY,
            risk_level=RiskLevel.MEDIUM,
            priority=2,
            tenant_id="team-a",
        )
        event_log.upsert_intent(db_path, intent)
        result = analytics.risk_review(db_path, "rev-no-sim")

        assert result["intent_id"] == "rev-no-sim"
        assert result["simulation"] is None
        assert result["risk"] is None
        assert result["diagnostics"] == []


class TestRunCalibration:
    """analytics.run_calibration() calibrates from historical data."""

    def test_calibration_with_data(self, db_path, tmp_path):
        """Calibration produces new profiles from historical risk events."""
        _seed_full_pipeline(db_path, n=20)
        output = tmp_path / "calibrated.json"
        result = analytics.run_calibration(db_path, output_path=str(output))

        assert result["data_points"] == 20
        assert "calibrated_profiles" in result
        assert "low" in result["calibrated_profiles"]
        assert "high" in result["calibrated_profiles"]
        assert output.exists()

        # Verify the file is valid JSON with profile data
        saved = json.loads(output.read_text())
        assert "low" in saved
        assert "entropy_budget" in saved["low"]

        # Verify calibration event recorded
        events = event_log.query(db_path, event_type="calibration.completed")
        assert len(events) == 1
        assert events[0]["payload"]["data_points"] == 20

    def test_calibration_no_data(self, db_path, tmp_path):
        """Calibration with no data returns default profiles."""
        output = tmp_path / "calibrated_empty.json"
        result = analytics.run_calibration(db_path, output_path=str(output))

        assert result["data_points"] == 0
        assert output.exists()


class TestSaveArchaeologySnapshot:
    """analytics.save_archaeology_snapshot() persists report to disk."""

    def test_save_and_load(self, tmp_path):
        report = {
            "commits_analyzed": 100,
            "hotspots": [{"file": "core.py", "changes": 25}],
            "coupling": [{"file_a": "a.py", "file_b": "b.py", "co_changes": 10}],
        }
        output = tmp_path / "snapshot.json"
        path = analytics.save_archaeology_snapshot(report, output_path=str(output))

        assert output.exists()
        saved = json.loads(output.read_text())
        assert saved["commits_analyzed"] == 100
        assert len(saved["hotspots"]) == 1
