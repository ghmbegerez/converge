"""End-to-end tests using a real git repository.

These tests create actual git repos with branches, commits, and conflicts,
then exercise the full converge pipeline: simulate → validate → queue → merge.
"""

import subprocess
from pathlib import Path

import pytest

from converge import engine, event_log
from converge.models import Event, Intent, RiskLevel, Status


# ---------------------------------------------------------------------------
# Fixtures: real git repo
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo,
        capture_output=True, text=True, check=True,
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
             "HOME": str(repo), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"},
    )


@pytest.fixture
def git_repo(tmp_path) -> Path:
    """Create a real git repository with a main branch and initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "test")

    # Initial commit on main
    (repo / "README.md").write_text("# Test Repo\n")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def main():\n    pass\n")
    (repo / "src" / "utils.py").write_text("def helper():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Initial commit")
    return repo


@pytest.fixture
def clean_branch(git_repo) -> Path:
    """Create a feature branch with non-conflicting changes."""
    _git(git_repo, "checkout", "-b", "feature/clean")
    (git_repo / "src" / "new_feature.py").write_text("def feature():\n    return 42\n")
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "Add new feature")
    _git(git_repo, "checkout", "main")
    return git_repo


@pytest.fixture
def conflict_branch(git_repo) -> Path:
    """Create a feature branch that conflicts with main."""
    # Create the branch
    _git(git_repo, "checkout", "-b", "feature/conflict")
    (git_repo / "src" / "app.py").write_text("def main():\n    print('from branch')\n")
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "Change app.py on branch")

    # Go back to main and make a conflicting change
    _git(git_repo, "checkout", "main")
    (git_repo / "src" / "app.py").write_text("def main():\n    print('from main')\n")
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "Change app.py on main")
    return git_repo


# ---------------------------------------------------------------------------
# E2E: Clean merge pipeline
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestE2ECleanMerge:
    """Full pipeline with a mergeable branch."""

    def test_simulate_clean_merge(self, clean_branch, db_path):
        """Simulation detects a clean merge."""
        sim = engine.simulate("feature/clean", "main",
                              intent_id="e2e-001", cwd=clean_branch)
        assert sim.mergeable is True
        assert len(sim.conflicts) == 0
        assert "new_feature.py" in " ".join(sim.files_changed)

    def test_validate_clean_intent(self, clean_branch, db_path):
        """Full validation passes for a clean branch."""
        intent = Intent(
            id="e2e-clean-001",
            source="feature/clean",
            target="main",
            status=Status.READY,
            risk_level=RiskLevel.LOW,
            priority=2,
            tenant_id="team-e2e",
        )
        event_log.upsert_intent(intent)

        result = engine.validate_intent(intent, skip_checks=True,
                                        cwd=clean_branch)
        assert result["decision"] == "validated"
        assert result["simulation"]["mergeable"] is True
        assert "trace_id" in result
        assert result["risk"]["risk_score"] >= 0

        # Verify intent status changed
        loaded = event_log.get_intent("e2e-clean-001")
        assert loaded.status == Status.VALIDATED

    def test_full_pipeline_validate_queue_merge(self, clean_branch, db_path):
        """E2E: create intent → validate → queue → confirm merge."""
        intent = Intent(
            id="e2e-pipeline-001",
            source="feature/clean",
            target="main",
            status=Status.READY,
            risk_level=RiskLevel.LOW,
            priority=1,
            tenant_id="team-e2e",
        )
        event_log.upsert_intent(intent)

        # Step 1: Validate
        result = engine.validate_intent(intent, skip_checks=True,
                                        cwd=clean_branch)
        assert result["decision"] == "validated"

        # Step 2: Confirm merge
        merge_result = engine.confirm_merge("e2e-pipeline-001",
                                            merged_commit="e2e-sha-001")
        assert merge_result["status"] == "MERGED"

        # Verify final state
        loaded = event_log.get_intent("e2e-pipeline-001")
        assert loaded.status == Status.MERGED

        # Verify event trail
        events = event_log.query(intent_id="e2e-pipeline-001", limit=50)
        event_types = [e["event_type"] for e in events]
        assert "simulation.completed" in event_types
        assert "risk.evaluated" in event_types
        assert "policy.evaluated" in event_types
        assert "intent.validated" in event_types
        assert "intent.merged" in event_types


# ---------------------------------------------------------------------------
# E2E: Conflict detection
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestE2EConflict:
    """Pipeline with a conflicting branch."""

    def test_simulate_conflict(self, conflict_branch, db_path):
        """Simulation detects merge conflicts."""
        sim = engine.simulate("feature/conflict", "main",
                              intent_id="e2e-conflict-001", cwd=conflict_branch)
        assert sim.mergeable is False
        assert len(sim.conflicts) > 0

    def test_validate_blocks_on_conflict(self, conflict_branch, db_path):
        """Validation blocks when merge has conflicts."""
        intent = Intent(
            id="e2e-conflict-002",
            source="feature/conflict",
            target="main",
            status=Status.READY,
            risk_level=RiskLevel.MEDIUM,
            priority=2,
            tenant_id="team-e2e",
        )
        event_log.upsert_intent(intent)

        result = engine.validate_intent(intent, skip_checks=True,
                                        cwd=conflict_branch)
        assert result["decision"] == "blocked"
        assert "conflict" in result["reason"].lower()
        assert "trace_id" in result


# ---------------------------------------------------------------------------
# E2E: Queue processing with retries (Invariant 3)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestE2EQueueRetries:
    """Queue processing with bounded retry."""

    def test_queue_rejects_after_max_retries(self, conflict_branch, db_path):
        """Invariant 3: intent rejected after max retries."""
        intent = Intent(
            id="e2e-retry-001",
            source="feature/conflict",
            target="main",
            status=Status.VALIDATED,
            risk_level=RiskLevel.MEDIUM,
            priority=2,
            tenant_id="team-e2e",
        )
        event_log.upsert_intent(intent)
        event_log.update_intent_status("e2e-retry-001", Status.VALIDATED, retries=3)

        results = engine.process_queue(
        max_retries=3,
            use_last_simulation=True,
            skip_checks=True,
            cwd=conflict_branch,
        )
        assert len(results) >= 1
        assert results[0]["decision"] == "rejected"

        loaded = event_log.get_intent("e2e-retry-001")
        assert loaded.status == Status.REJECTED

    def test_queue_processes_clean_intent(self, clean_branch, db_path):
        """Queue processes and validates a clean intent."""
        intent = Intent(
            id="e2e-queue-clean",
            source="feature/clean",
            target="main",
            status=Status.VALIDATED,
            risk_level=RiskLevel.LOW,
            priority=1,
            tenant_id="team-e2e",
        )
        event_log.upsert_intent(intent)

        results = engine.process_queue(
        skip_checks=True,
            cwd=clean_branch,
        )
        assert len(results) >= 1
        # Should validate successfully → QUEUED
        assert results[0]["decision"] in ("validated", "merged")


# ---------------------------------------------------------------------------
# E2E: Risk evaluation with real simulation data
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestE2ERiskEvaluation:
    """Risk evaluation using real git simulation data."""

    def test_risk_signals_populated(self, clean_branch, db_path):
        """Risk evaluation produces all 4 signals from real simulation."""
        intent = Intent(
            id="e2e-risk-001",
            source="feature/clean",
            target="main",
            status=Status.READY,
            risk_level=RiskLevel.MEDIUM,
            priority=2,
            technical={"scope_hint": ["src"]},
            tenant_id="team-e2e",
        )
        event_log.upsert_intent(intent)

        result = engine.validate_intent(intent, skip_checks=True,
                                        cwd=clean_branch)
        assert result["decision"] == "validated"

        risk = result["risk"]
        assert "signals" in risk
        assert "entropic_load" in risk["signals"]
        assert "contextual_value" in risk["signals"]
        assert "complexity_delta" in risk["signals"]
        assert "path_dependence" in risk["signals"]
        assert "graph_metrics" in risk
        assert risk["graph_metrics"]["nodes"] > 0

    def test_event_trail_complete(self, clean_branch, db_path):
        """All events have trace_id for forensic correlation."""
        intent = Intent(
            id="e2e-trace-001",
            source="feature/clean",
            target="main",
            status=Status.READY,
            risk_level=RiskLevel.LOW,
            priority=1,
            tenant_id="team-e2e",
        )
        event_log.upsert_intent(intent)

        result = engine.validate_intent(intent, skip_checks=True,
                                        cwd=clean_branch)
        trace_id = result["trace_id"]

        # All events for this intent should reference the same trace_id
        risk_events = event_log.query(event_type="risk.evaluated",
                                      intent_id="e2e-trace-001")
        assert risk_events[0]["evidence"]["trace_id"] == trace_id

        policy_events = event_log.query(event_type="policy.evaluated",
                                        intent_id="e2e-trace-001")
        assert policy_events[0]["payload"]["trace_id"] == trace_id
