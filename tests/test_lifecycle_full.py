"""Full lifecycle integration tests.

These tests exercise the complete intent lifecycle through the engine
with real git repos and an in-memory-equivalent SQLite database (via
tmp_path, consistent with the existing test infrastructure).

Test scenarios:
  1. Full lifecycle: create -> simulate -> validate -> queue -> merge
  2. Reject after max retries
  3. Dependency blocking: intent B blocked until intent A merges
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from converge import engine, event_log
from converge.models import Event, EventType, Intent, RiskLevel, Status

# ---------------------------------------------------------------------------
# Git repo fixtures
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, check=True,
    )


@pytest.fixture
def git_repo(tmp_path) -> Path:
    """Create a git repo with main + feature/alpha (non-conflicting)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@converge.dev", cwd=repo)
    _git("config", "user.name", "Converge Test", cwd=repo)

    # Initial commit on main
    (repo / "README.md").write_text("# Project\n")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def main():\n    pass\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "Initial commit", cwd=repo)

    # Feature branch with a new file (non-conflicting)
    _git("checkout", "-b", "feature/alpha", cwd=repo)
    (repo / "src" / "feature_alpha.py").write_text("def alpha():\n    return 'alpha'\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "Add alpha feature", cwd=repo)

    # Back to main
    _git("checkout", "main", cwd=repo)

    return repo


@pytest.fixture
def git_repo_conflict(tmp_path) -> Path:
    """Create a git repo where feature/conflict-b conflicts with main
    after feature/conflict-a has been merged."""
    repo = tmp_path / "repo_conflict"
    repo.mkdir()

    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@converge.dev", cwd=repo)
    _git("config", "user.name", "Converge Test", cwd=repo)

    (repo / "shared.py").write_text("original\n")
    _git("add", "shared.py", cwd=repo)
    _git("commit", "-m", "Initial commit", cwd=repo)

    # Feature A modifies shared.py
    _git("checkout", "-b", "feature/conflict-a", cwd=repo)
    (repo / "shared.py").write_text("version A\n")
    _git("add", "shared.py", cwd=repo)
    _git("commit", "-m", "Feature A change", cwd=repo)

    # Feature B also modifies shared.py (conflicts with A after A merges)
    _git("checkout", "main", cwd=repo)
    _git("checkout", "-b", "feature/conflict-b", cwd=repo)
    (repo / "shared.py").write_text("version B\n")
    _git("add", "shared.py", cwd=repo)
    _git("commit", "-m", "Feature B change", cwd=repo)

    _git("checkout", "main", cwd=repo)

    return repo


@pytest.fixture
def git_repo_two_features(tmp_path) -> Path:
    """Create a git repo with main + two non-conflicting feature branches."""
    repo = tmp_path / "repo_deps"
    repo.mkdir()

    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@converge.dev", cwd=repo)
    _git("config", "user.name", "Converge Test", cwd=repo)

    (repo / "README.md").write_text("# Project\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "Initial commit", cwd=repo)

    # Feature A: adds file_a.py
    _git("checkout", "-b", "feature/a", cwd=repo)
    (repo / "file_a.py").write_text("def a():\n    return 'A'\n")
    _git("add", "file_a.py", cwd=repo)
    _git("commit", "-m", "Add feature A", cwd=repo)

    # Back to main, create feature B: adds file_b.py
    _git("checkout", "main", cwd=repo)
    _git("checkout", "-b", "feature/b", cwd=repo)
    (repo / "file_b.py").write_text("def b():\n    return 'B'\n")
    _git("add", "file_b.py", cwd=repo)
    _git("commit", "-m", "Add feature B", cwd=repo)

    _git("checkout", "main", cwd=repo)

    return repo


# ---------------------------------------------------------------------------
# Test 1: Full lifecycle — create -> simulate -> validate -> queue -> merge
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestFullLifecycle:
    """Complete lifecycle: intent created, validated with real git simulation,
    queued, processed with auto_confirm, and confirmed as MERGED."""

    def test_create_validate_queue_merge(self, db_path, git_repo):
        """Full lifecycle: create intent -> simulate -> validate -> queue ->
        process queue (auto_confirm) -> verify MERGED status and event trail."""

        # ---- Step 1: Create an intent and persist it ----
        intent = Intent(
            id="lifecycle-full-001",
            source="feature/alpha",
            target="main",
            status=Status.READY,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=1,
            tenant_id="team-lifecycle",
        )
        event_log.upsert_intent(intent)
        event_log.append(Event(
            event_type=EventType.INTENT_CREATED,
            intent_id=intent.id,
            tenant_id=intent.tenant_id,
            payload={"source": intent.source, "target": intent.target},
        ))

        # Verify intent persisted
        loaded = event_log.get_intent("lifecycle-full-001")
        assert loaded is not None
        assert loaded.status == Status.READY

        # ---- Step 2: Simulate the merge ----
        sim = engine.simulate(
            "feature/alpha", "main",
            intent_id=intent.id,
            tenant_id=intent.tenant_id,
            cwd=str(git_repo),
        )
        assert sim.mergeable is True
        assert len(sim.conflicts) == 0
        assert any("feature_alpha.py" in f for f in sim.files_changed)

        # ---- Step 3: Validate the intent (real simulation + policy) ----
        decision = engine.validate_intent(
            intent,
            skip_checks=True,
            cwd=str(git_repo),
        )
        assert decision["decision"] == "validated", f"Validation failed: {decision.get('reason')}"
        assert decision["simulation"]["mergeable"] is True
        assert "trace_id" in decision
        assert decision["risk"]["risk_score"] >= 0

        # Intent status updated to VALIDATED
        loaded = event_log.get_intent("lifecycle-full-001")
        assert loaded.status == Status.VALIDATED

        # ---- Step 4: Process queue with auto_confirm (queue + merge) ----
        main_head_before = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

        results = engine.process_queue(
            auto_confirm=True,
            skip_checks=True,
            use_last_simulation=True,
            cwd=str(git_repo),
        )

        # Find our intent's result
        our_result = next(
            (r for r in results if r.get("intent_id") == "lifecycle-full-001"),
            None,
        )
        assert our_result is not None, f"Intent not processed. Results: {results}"
        assert our_result["decision"] == "merged", f"Expected merged, got: {our_result}"
        assert "merged_commit" in our_result

        # ---- Step 5: Verify git state ----
        main_head_after = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

        assert main_head_after != main_head_before, "main should have advanced"
        assert main_head_after == our_result["merged_commit"]

        # feature_alpha.py reachable from main
        ls_result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "main"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        assert "feature_alpha.py" in ls_result.stdout

        # ---- Step 6: Verify final status is MERGED ----
        loaded = event_log.get_intent("lifecycle-full-001")
        assert loaded.status == Status.MERGED

        # ---- Step 7: Verify complete event trail ----
        events = event_log.query(intent_id="lifecycle-full-001", limit=100)
        event_types = [e["event_type"] for e in events]

        assert "intent.created" in event_types
        assert "simulation.completed" in event_types
        assert "risk.evaluated" in event_types
        assert "policy.evaluated" in event_types
        assert "intent.validated" in event_types
        assert "intent.merged" in event_types

        # Merged event contains the commit SHA
        merged_events = event_log.query(
            event_type="intent.merged", intent_id="lifecycle-full-001",
        )
        assert len(merged_events) >= 1
        assert merged_events[0]["payload"]["merged_commit"] == our_result["merged_commit"]

        # Queue processed event was emitted
        queue_events = event_log.query(event_type="queue.processed")
        assert len(queue_events) >= 1


# ---------------------------------------------------------------------------
# Test 2: Reject after max retries
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLifecycleRejectAfterMaxRetries:
    """Invariant 3: an intent that consistently fails validation is rejected
    after max_retries attempts."""

    def test_reject_after_max_retries_conflict(self, db_path, git_repo_conflict):
        """Create an intent on a conflicting branch, process queue multiple
        times, and verify it is ultimately REJECTED after max retries."""

        repo = git_repo_conflict

        # First merge conflict-a so conflict-b will always fail simulation
        from converge.scm import execute_merge_safe
        execute_merge_safe("feature/conflict-a", "main", cwd=repo)

        # Create intent for conflict-b — start at retries=0
        intent = Intent(
            id="lifecycle-retry-001",
            source="feature/conflict-b",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=1,
            retries=0,
            tenant_id="team-retry",
        )
        event_log.upsert_intent(intent)

        max_retries = 3

        # Process queue repeatedly. Each attempt should fail (conflict) and
        # increment retries. After max_retries total blocked attempts the
        # intent should transition to REJECTED.
        for _attempt in range(max_retries + 1):
            current = event_log.get_intent("lifecycle-retry-001")
            if current.status == Status.REJECTED:
                break

            # Ensure the intent is in VALIDATED to be picked up by queue
            if current.status not in (Status.VALIDATED,):
                event_log.update_intent_status(
                    "lifecycle-retry-001", Status.VALIDATED,
                    retries=current.retries,
                )

            engine.process_queue(
                auto_confirm=True,
                skip_checks=True,
                max_retries=max_retries,
                cwd=str(repo),
            )

        # Verify final status is REJECTED
        loaded = event_log.get_intent("lifecycle-retry-001")
        assert loaded.status == Status.REJECTED, (
            f"Expected REJECTED after max retries, got {loaded.status.value} "
            f"(retries={loaded.retries})"
        )

        # Verify rejection event was emitted
        reject_events = event_log.query(
            event_type="intent.rejected", intent_id="lifecycle-retry-001",
        )
        assert len(reject_events) >= 1

    def test_reject_immediate_when_retries_at_max(self, db_path, git_repo_conflict):
        """An intent that already has retries >= max_retries is immediately
        rejected on the next queue processing attempt."""

        repo = git_repo_conflict

        # Merge conflict-a first
        from converge.scm import execute_merge_safe
        execute_merge_safe("feature/conflict-a", "main", cwd=repo)

        # Create intent already at max retries
        intent = Intent(
            id="lifecycle-retry-immediate-001",
            source="feature/conflict-b",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=1,
            retries=3,
            tenant_id="team-retry",
        )
        event_log.upsert_intent(intent)

        # Single queue run with max_retries=3 should immediately reject
        results = engine.process_queue(
            auto_confirm=True,
            skip_checks=True,
            max_retries=3,
            cwd=str(repo),
        )

        our_result = next(
            (r for r in results if r.get("intent_id") == "lifecycle-retry-immediate-001"),
            None,
        )
        assert our_result is not None
        assert our_result["decision"] == "rejected"
        assert "max_retries" in our_result.get("reason", "")

        loaded = event_log.get_intent("lifecycle-retry-immediate-001")
        assert loaded.status == Status.REJECTED

        # Rejection event emitted
        reject_events = event_log.query(
            event_type="intent.rejected",
            intent_id="lifecycle-retry-immediate-001",
        )
        assert len(reject_events) >= 1


# ---------------------------------------------------------------------------
# Test 3: Dependency blocking
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLifecycleDependencyBlocking:
    """Intent B depends on intent A. B should be blocked until A merges,
    then B should proceed normally."""

    def test_dependency_blocks_then_unblocks(self, db_path, git_repo_two_features):
        """Create two intents where B depends on A. When A is not yet MERGED,
        B should be dependency_blocked. After A is merged, B should proceed."""

        repo = git_repo_two_features

        # ---- Create intent A in READY state (not in queue yet) ----
        intent_a = Intent(
            id="dep-test:pr-a",
            source="feature/a",
            target="main",
            status=Status.READY,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=1,
            dependencies=[],
            tenant_id="team-deps",
        )
        # ---- Create intent B (depends on A) in VALIDATED state ----
        intent_b = Intent(
            id="dep-test:pr-b",
            source="feature/b",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=2,
            dependencies=["dep-test:pr-a"],
            tenant_id="team-deps",
        )
        event_log.upsert_intent(intent_a)
        event_log.upsert_intent(intent_b)

        # Seed simulation events for both (so use_last_simulation works)
        for iid, src in [("dep-test:pr-a", "feature/a"),
                         ("dep-test:pr-b", "feature/b")]:
            event_log.append(Event(
                event_type=EventType.SIMULATION_COMPLETED,
                intent_id=iid,
                payload={
                    "mergeable": True,
                    "conflicts": [],
                    "files_changed": [f"{src.split('/')[-1]}.py"],
                    "source": src,
                    "target": "main",
                },
            ))

        # ---- First queue run: only B is VALIDATED, A is READY ----
        # B should be dependency_blocked because A is not MERGED
        results_1 = engine.process_queue(
            auto_confirm=True,
            skip_checks=True,
            use_last_simulation=True,
            cwd=str(repo),
        )

        b_result = next(
            (r for r in results_1 if r.get("intent_id") == "dep-test:pr-b"),
            None,
        )

        # B should be blocked by dependency (A is READY, not MERGED)
        assert b_result is not None, f"Intent B not in results: {results_1}"
        assert b_result["decision"] == "dependency_blocked"
        assert "dep-test:pr-a" in b_result.get("unmet_dependencies", [])

        # Dependency blocked event was emitted for B
        dep_events = event_log.query(
            event_type="intent.dependency_blocked",
            intent_id="dep-test:pr-b",
        )
        assert len(dep_events) >= 1

        # ---- Now validate and merge A ----
        intent_a_loaded = event_log.get_intent("dep-test:pr-a")
        decision_a = engine.validate_intent(
            intent_a_loaded,
            skip_checks=True,
            cwd=str(repo),
        )
        assert decision_a["decision"] == "validated"

        # Process queue -- both A and B are VALIDATED now.
        # A is processed first (priority 1), merges with auto_confirm.
        # B is processed second (priority 2); since A is now MERGED in the
        # store, B's dependency is satisfied and B proceeds to merge too.
        results_after = engine.process_queue(
            auto_confirm=True,
            skip_checks=True,
            use_last_simulation=True,
            cwd=str(repo),
        )
        a_merge_result = next(
            (r for r in results_after if r.get("intent_id") == "dep-test:pr-a"),
            None,
        )
        assert a_merge_result is not None
        assert a_merge_result["decision"] == "merged"
        assert event_log.get_intent("dep-test:pr-a").status == Status.MERGED

        # B should also have been processed in this same run (its dependency
        # was satisfied within the sequential processing loop).
        b_result_2 = next(
            (r for r in results_after if r.get("intent_id") == "dep-test:pr-b"),
            None,
        )
        assert b_result_2 is not None, (
            f"Intent B not in queue run after A merged: {results_after}"
        )
        assert b_result_2["decision"] == "merged", (
            f"Expected B to merge after A merged. Got: {b_result_2}"
        )

        # ---- Verify final state ----
        assert event_log.get_intent("dep-test:pr-a").status == Status.MERGED
        assert event_log.get_intent("dep-test:pr-b").status == Status.MERGED

        # Both files reachable from main
        ls_result = subprocess.run(
            ["git", "ls-tree", "--name-only", "main"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        assert "file_a.py" in ls_result.stdout
        assert "file_b.py" in ls_result.stdout

    def test_dependency_blocked_multiple_unmet(self, db_path, git_repo_two_features):
        """An intent with multiple dependencies remains blocked when any
        dependency is not yet MERGED."""

        repo = git_repo_two_features

        # Create intents A and B in READY state (not MERGED)
        for iid, src in [("multi-dep:pr-a", "feature/a"),
                         ("multi-dep:pr-b", "feature/b")]:
            intent = Intent(
                id=iid,
                source=src,
                target="main",
                status=Status.READY,
                created_by="test",
                risk_level=RiskLevel.LOW,
                priority=1,
                tenant_id="team-deps",
            )
            event_log.upsert_intent(intent)

        # Create intent C (VALIDATED, depends on both A and B)
        intent_c = Intent(
            id="multi-dep:pr-c",
            source="feature/a",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=3,
            dependencies=["multi-dep:pr-a", "multi-dep:pr-b"],
            tenant_id="team-deps",
        )
        event_log.upsert_intent(intent_c)
        event_log.append(Event(
            event_type=EventType.SIMULATION_COMPLETED,
            intent_id="multi-dep:pr-c",
            payload={
                "mergeable": True,
                "conflicts": [],
                "files_changed": ["file_a.py"],
                "source": "feature/a",
                "target": "main",
            },
        ))

        # Process queue -- only C is VALIDATED, A and B are READY
        # C should be dependency_blocked because neither A nor B is MERGED
        results = engine.process_queue(
            auto_confirm=True,
            skip_checks=True,
            use_last_simulation=True,
            cwd=str(repo),
        )

        c_result = next(
            (r for r in results if r.get("intent_id") == "multi-dep:pr-c"),
            None,
        )
        assert c_result is not None
        assert c_result["decision"] == "dependency_blocked"
        assert "multi-dep:pr-a" in c_result.get("unmet_dependencies", [])
        assert "multi-dep:pr-b" in c_result.get("unmet_dependencies", [])
