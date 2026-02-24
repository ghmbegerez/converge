"""Tests for archaeology enhancements: link-enriched coupling, provenance, refresh (AR-07..AR-09)."""

import json
from pathlib import Path
from unittest.mock import patch

from conftest import make_intent

from converge import analytics, event_log
from converge.models import Intent, RiskLevel, Status


class TestCouplingProvenance:
    """AR-08: coupling data includes source and freshness metadata."""

    def test_snapshot_coupling_has_provenance(self, db_path, tmp_path):
        snapshot = {
            "coupling": [{"file_a": "a.py", "file_b": "b.py", "co_changes": 5}],
            "timestamp": "2026-01-01T00:00:00Z",
        }
        snapshot_path = tmp_path / ".converge" / "archaeology_snapshot.json"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps(snapshot))

        with patch.object(analytics, "_SNAPSHOT_PATH", snapshot_path):
            coupling = analytics.load_coupling_data()

        assert len(coupling) == 1
        assert coupling[0]["source"] == "snapshot"
        assert coupling[0]["freshness"] == "2026-01-01T00:00:00Z"

    def test_gitlog_coupling_has_provenance(self, db_path):
        entries = [
            {"author": "dev", "files": ["a.py", "b.py"]},
            {"author": "dev", "files": ["a.py", "b.py"]},
        ]
        with patch.object(analytics, "_load_snapshot", return_value=None), \
             patch.object(analytics.scm, "log_entries", return_value=entries):
            coupling = analytics.load_coupling_data()

        assert len(coupling) >= 1
        assert coupling[0]["source"] == "git-log"
        assert "freshness" in coupling[0]


class TestLinkEnrichedCoupling:
    """AR-07: coupling enriched with intent commit link data."""

    def test_link_coupling_merged_into_snapshot(self, db_path, tmp_path):
        # Create intents with overlapping scope hints and commit links
        make_intent("lc-001", technical={"scope_hint": ["auth", "api"]})
        make_intent("lc-002", technical={"scope_hint": ["auth", "api"]})
        event_log.upsert_commit_link("lc-001", "org/repo", "aaa", "head")
        event_log.upsert_commit_link("lc-002", "org/repo", "bbb", "head")

        # Provide a snapshot with existing coupling
        snapshot = {
            "coupling": [{"file_a": "auth", "file_b": "db", "co_changes": 3}],
            "timestamp": "2026-01-01T00:00:00Z",
        }
        snapshot_path = tmp_path / ".converge" / "archaeology_snapshot.json"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps(snapshot))

        with patch.object(analytics, "_SNAPSHOT_PATH", snapshot_path):
            coupling = analytics.load_coupling_data()

        # Should have both snapshot coupling and link-derived coupling
        pairs = {(c["file_a"], c["file_b"]) for c in coupling}
        assert ("auth", "db") in pairs  # from snapshot
        assert ("api", "auth") in pairs  # from link-derived (sorted order)

    def test_no_links_returns_snapshot_only(self, db_path, tmp_path):
        snapshot = {
            "coupling": [{"file_a": "x.py", "file_b": "y.py", "co_changes": 2}],
            "timestamp": "2026-01-01T00:00:00Z",
        }
        snapshot_path = tmp_path / ".converge" / "archaeology_snapshot.json"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps(snapshot))

        with patch.object(analytics, "_SNAPSHOT_PATH", snapshot_path):
            coupling = analytics.load_coupling_data()

        assert len(coupling) == 1
        assert coupling[0]["file_a"] == "x.py"

    def test_link_coupling_without_snapshot(self, db_path):
        make_intent("lc-003", technical={"scope_hint": ["core", "utils"]})
        make_intent("lc-004", technical={"scope_hint": ["core", "utils"]})
        event_log.upsert_commit_link("lc-003", "org/repo", "ccc", "head")
        event_log.upsert_commit_link("lc-004", "org/repo", "ddd", "head")

        with patch.object(analytics, "_load_snapshot", return_value=None), \
             patch.object(analytics.scm, "log_entries", return_value=[]):
            coupling = analytics.load_coupling_data()

        # Only link-based coupling (git log returned nothing)
        assert len(coupling) >= 1
        assert coupling[0]["source"] == "linked-history"


class TestSnapshotRefresh:
    """AR-09: refresh snapshot and validate."""

    def test_refresh_valid_snapshot(self, db_path, tmp_path):
        entries = [
            {"author": "dev1", "files": ["a.py", "b.py"]},
            {"author": "dev2", "files": ["c.py"]},
        ] * 10  # enough data for valid snapshot
        output = tmp_path / "snapshot.json"

        with patch.object(analytics.scm, "log_entries", return_value=entries):
            result = analytics.refresh_snapshot(output_path=str(output))

        assert result["valid"] is True
        assert result["commits_analyzed"] == 20
        assert result["hotspot_count"] > 0
        assert result["author_count"] > 0
        assert output.exists()

    def test_refresh_no_history_invalid(self, db_path):
        with patch.object(analytics.scm, "log_entries", return_value=[]):
            result = analytics.refresh_snapshot()

        assert result["valid"] is False

    def test_validate_snapshot_detects_issues(self, db_path):
        report = {"commits_analyzed": 0, "hotspots": [], "coupling": [], "authors": [], "bus_factor": 0}
        result = analytics._validate_snapshot(report)
        assert result["valid"] is False
        assert len(result["issues"]) >= 2  # zero commits + no hotspots + no authors + bus factor
