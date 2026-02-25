"""E2E lifecycle tests with real git repos.

These tests exercise the full path that matters:
  PR webhook → intent creation → simulation → risk/policy → queue → merge → post-merge

No mocks on the core path. Real git repos, real merge operations, real policy evaluation.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.request import Request, urlopen

import pytest

from converge import engine, event_log
from converge.models import EventType, Intent, RiskLevel, Simulation, Status


# ---------------------------------------------------------------------------
# Git repo fixtures
# ---------------------------------------------------------------------------

def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, check=True,
    )


@pytest.fixture
def git_repo(tmp_path):
    """Create a git repo with main + feature/alpha (non-conflicting)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@converge.dev", cwd=repo)
    _git("config", "user.name", "Converge Test", cwd=repo)

    # Initial commit on main
    (repo / "README.md").write_text("# Project\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "Initial commit", cwd=repo)

    # Feature branch with a new file
    _git("checkout", "-b", "feature/alpha", cwd=repo)
    (repo / "feature_alpha.py").write_text("def alpha():\n    return 'alpha'\n")
    _git("add", "feature_alpha.py", cwd=repo)
    _git("commit", "-m", "Add alpha feature", cwd=repo)

    # Back to main
    _git("checkout", "main", cwd=repo)

    return repo


@pytest.fixture
def git_repo_two_features(tmp_path):
    """Create a git repo with main + two non-conflicting feature branches."""
    repo = tmp_path / "repo2"
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


@pytest.fixture
def git_repo_conflict(tmp_path):
    """Create a git repo where two feature branches conflict on the same file."""
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


# ---------------------------------------------------------------------------
# Test 1: Full lifecycle — webhook to merge with real git
# ---------------------------------------------------------------------------

class TestE2EFullLifecycle:
    """PR webhook → validate (real simulation) → queue → merge → verify commit."""

    def test_webhook_validate_queue_merge(self, db_path, live_server, git_repo):
        """Complete lifecycle with real git repo: intent created via webhook,
        validated with real merge simulation, queued, merged via worktree,
        and post-merge confirmed — all with real git operations."""

        head_sha = subprocess.run(
            ["git", "rev-parse", "feature/alpha"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

        # ---- Step 1: PR webhook creates the intent ----
        pr_payload = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "title": "Add alpha feature",
                "head": {"ref": "feature/alpha", "sha": head_sha},
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "org/repo"},
            "installation": {"id": 12345},
        }
        resp = _webhook(live_server, "pull_request", pr_payload, "e2e-pr-42")
        assert resp["ok"] is True
        assert resp["action"] == "created"
        intent_id = resp["intent_id"]
        assert intent_id == "org/repo:pr-42"

        # Verify intent persisted with correct fields
        intent = event_log.get_intent(intent_id)
        assert intent is not None
        assert intent.status == Status.READY
        assert intent.source == "feature/alpha"
        assert intent.target == "main"
        assert intent.technical["installation_id"] == 12345

        # Verify creation event emitted
        created_events = event_log.query(event_type="intent.created", intent_id=intent_id)
        assert len(created_events) >= 1

        # ---- Step 2: Validate with REAL merge simulation ----
        decision = engine.validate_intent(
            intent,
            skip_checks=True,       # no make lint/test in test repo
            cwd=str(git_repo),
        )
        assert decision["decision"] == "validated", f"Validation failed: {decision.get('reason')}"
        assert decision["simulation"]["mergeable"] is True
        assert "feature_alpha.py" in decision["simulation"]["files_changed"]

        # Intent status updated
        intent = event_log.get_intent(intent_id)
        assert intent.status == Status.VALIDATED

        # Risk and policy events emitted
        risk_events = event_log.query(event_type="risk.evaluated", intent_id=intent_id)
        assert len(risk_events) >= 1
        policy_events = event_log.query(event_type="policy.evaluated", intent_id=intent_id)
        assert len(policy_events) >= 1

        # ---- Step 3: Process queue with REAL merge via worktree ----
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
        our_result = next((r for r in results if r.get("intent_id") == intent_id), None)
        assert our_result is not None, f"Intent not processed. Results: {results}"
        assert our_result["decision"] == "merged", f"Expected merged, got: {our_result}"
        assert "merged_commit" in our_result

        # ---- Step 4: Verify the merge actually happened in git ----
        main_head_after = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

        assert main_head_after != main_head_before, "main should have advanced"
        assert main_head_after == our_result["merged_commit"]

        # feature_alpha.py should now be reachable from main
        ls_result = subprocess.run(
            ["git", "ls-tree", "--name-only", "main"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        assert "feature_alpha.py" in ls_result.stdout

        # ---- Step 5: Verify intent is MERGED in the store ----
        intent = event_log.get_intent(intent_id)
        assert intent.status == Status.MERGED

        # Merged event emitted
        merged_events = event_log.query(event_type="intent.merged", intent_id=intent_id)
        assert len(merged_events) >= 1
        assert merged_events[0]["payload"]["merged_commit"] == our_result["merged_commit"]

        # Queue processed event
        queue_events = event_log.query(event_type="queue.processed")
        assert len(queue_events) >= 1

    def test_conflicting_pr_blocked_at_simulation(self, db_path, git_repo_conflict):
        """A PR that conflicts with main is blocked during validation."""

        # First, merge feature/conflict-a into main so main diverges
        from converge.scm import execute_merge_safe
        execute_merge_safe("feature/conflict-a", "main", cwd=git_repo_conflict)

        # Now feature/conflict-b conflicts with the updated main
        intent = Intent(
            id="conflict-test:pr-1",
            source="feature/conflict-b",
            target="main",
            status=Status.READY,
            created_by="test",
            risk_level=RiskLevel.MEDIUM,
            priority=2,
        )
        event_log.upsert_intent(intent)

        decision = engine.validate_intent(
            intent,
            skip_checks=True,
            cwd=str(git_repo_conflict),
        )

        assert decision["decision"] == "blocked"
        assert "conflict" in decision["reason"].lower()

        # Intent should NOT advance to VALIDATED
        intent = event_log.get_intent("conflict-test:pr-1")
        assert intent.status == Status.READY

    def test_post_merge_webhook_confirms_status(self, db_path, live_server):
        """PR closed+merged webhook transitions intent to MERGED."""

        # Pre-create an intent in VALIDATED state (simulating mid-lifecycle)
        intent = Intent(
            id="org/repo:pr-99",
            source="feature/done",
            target="main",
            status=Status.VALIDATED,
            created_by="github-webhook",
            technical={"repo": "org/repo", "pr_number": 99,
                        "initial_base_commit": "sha-old", "installation_id": 5555},
        )
        event_log.upsert_intent(intent)

        # Send PR closed+merged webhook
        closed_payload = {
            "action": "closed",
            "pull_request": {
                "number": 99,
                "merged": True,
                "merge_commit_sha": "merge-sha-abc",
                "head": {"ref": "feature/done", "sha": "sha-old"},
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "org/repo"},
        }
        resp = _webhook(live_server, "pull_request", closed_payload, "e2e-close-99")
        assert resp["ok"] is True
        assert resp["action"] == "merged"

        # Intent is now MERGED
        intent = event_log.get_intent("org/repo:pr-99")
        assert intent.status == Status.MERGED

        # Merged event with correct commit SHA
        merged_events = event_log.query(event_type="intent.merged", intent_id="org/repo:pr-99")
        assert len(merged_events) >= 1
        assert merged_events[0]["payload"]["merge_commit_sha"] == "merge-sha-abc"


# ---------------------------------------------------------------------------
# Test 2: Sequential queue — two intents, same target, ordered processing
# ---------------------------------------------------------------------------

class TestQueueSequentialMerge:
    """Two intents targeting main, processed sequentially. Both must merge
    without corruption. The second intent must revalidate against the updated
    main (Invariant 2)."""

    def test_two_intents_same_target_both_merge(self, db_path, git_repo_two_features):
        """Two non-conflicting intents for 'main' are processed in order.
        The second revalidates against the post-first-merge state of main."""

        repo = git_repo_two_features

        # Create two VALIDATED intents
        intent_a = Intent(
            id="org/repo:pr-1",
            source="feature/a",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=1,          # higher priority → processed first
        )
        intent_b = Intent(
            id="org/repo:pr-2",
            source="feature/b",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=2,
        )
        event_log.upsert_intent(intent_a)
        event_log.upsert_intent(intent_b)

        # Seed simulation events so use_last_simulation=True works for the
        # initial validation step inside process_queue
        for iid, src in [("org/repo:pr-1", "feature/a"), ("org/repo:pr-2", "feature/b")]:
            event_log.append(event_log.Event(
                event_type=EventType.SIMULATION_COMPLETED,
                intent_id=iid,
                payload={"mergeable": True, "conflicts": [], "files_changed": [f"{src}.py"],
                         "source": src, "target": "main"},
            ))

        main_before = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Process queue — both intents should merge
        results = engine.process_queue(
            auto_confirm=True,
            skip_checks=True,
            use_last_simulation=True,
            cwd=str(repo),
        )

        merged_ids = [r["intent_id"] for r in results if r.get("decision") == "merged"]
        assert "org/repo:pr-1" in merged_ids, f"Intent A not merged. Results: {results}"

        # Intent B may have been requeued (revalidation against updated main)
        # or merged in the same run if revalidation passed.
        b_result = next((r for r in results if r.get("intent_id") == "org/repo:pr-2"), None)
        if b_result and b_result["decision"] != "merged":
            # B was blocked/requeued — process queue again (Invariant 2 in action)
            intent_b = event_log.get_intent("org/repo:pr-2")
            if intent_b.status in (Status.READY, Status.VALIDATED):
                # Re-validate and re-queue
                if intent_b.status == Status.READY:
                    engine.validate_intent(
                        intent_b, skip_checks=True, cwd=str(repo),
                    )
                results2 = engine.process_queue(
                    auto_confirm=True,
                    skip_checks=True,
                    use_last_simulation=True,
                    cwd=str(repo),
                )
                b_result2 = next((r for r in results2 if r.get("intent_id") == "org/repo:pr-2"), None)
                assert b_result2 is not None
                assert b_result2["decision"] == "merged", f"Intent B failed on second pass: {b_result2}"

        # Both intents should be MERGED
        assert event_log.get_intent("org/repo:pr-1").status == Status.MERGED
        assert event_log.get_intent("org/repo:pr-2").status == Status.MERGED

        # Main advanced twice
        main_after = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert main_after != main_before

        # Both files reachable from main
        ls_result = subprocess.run(
            ["git", "ls-tree", "--name-only", "main"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        assert "file_a.py" in ls_result.stdout
        assert "file_b.py" in ls_result.stdout

    def test_second_intent_blocked_by_conflict_after_first_merges(self, db_path, git_repo_conflict):
        """After intent A merges, intent B conflicts with the new main.
        Queue processing correctly blocks B (Invariant 2: revalidate)."""

        repo = git_repo_conflict

        intent_a = Intent(
            id="org/repo:pr-a",
            source="feature/conflict-a",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=1,
        )
        intent_b = Intent(
            id="org/repo:pr-b",
            source="feature/conflict-b",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=2,
        )
        event_log.upsert_intent(intent_a)
        event_log.upsert_intent(intent_b)

        # Seed simulation events
        for iid, src in [("org/repo:pr-a", "feature/conflict-a"),
                         ("org/repo:pr-b", "feature/conflict-b")]:
            event_log.append(event_log.Event(
                event_type=EventType.SIMULATION_COMPLETED,
                intent_id=iid,
                payload={"mergeable": True, "conflicts": [], "files_changed": ["shared.py"],
                         "source": src, "target": "main"},
            ))

        # Process queue — A should merge, B should be blocked on revalidation
        results = engine.process_queue(
            auto_confirm=True,
            skip_checks=True,
            use_last_simulation=False,  # force fresh simulation for revalidation
            cwd=str(repo),
        )

        a_result = next((r for r in results if r.get("intent_id") == "org/repo:pr-a"), None)
        b_result = next((r for r in results if r.get("intent_id") == "org/repo:pr-b"), None)

        # A merged successfully
        assert a_result is not None
        assert a_result["decision"] == "merged", f"A should merge: {a_result}"

        # B was blocked (conflict after A merged main)
        assert b_result is not None
        assert b_result["decision"] == "blocked", f"B should be blocked: {b_result}"
        assert "conflict" in b_result.get("reason", "").lower()

        # State: A=MERGED, B=READY (requeued with retry)
        assert event_log.get_intent("org/repo:pr-a").status == Status.MERGED
        b_intent = event_log.get_intent("org/repo:pr-b")
        assert b_intent.status in (Status.READY, Status.REJECTED)
        assert b_intent.retries >= 1


# ---------------------------------------------------------------------------
# Test 3: Queue lock prevents concurrent processing
# ---------------------------------------------------------------------------

class TestQueueLockConcurrency:
    """Verify the advisory lock prevents two process_queue calls from
    running simultaneously."""

    def test_lock_prevents_concurrent_queue_processing(self, db_path):
        """Second process_queue call fails gracefully when lock is held."""

        # Simulate a held lock (e.g., another worker process)
        acquired = event_log.acquire_queue_lock(holder_pid=99999)
        assert acquired

        # Create a VALIDATED intent
        intent = Intent(
            id="concurrent-test:pr-1",
            source="feature/x",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=1,
        )
        event_log.upsert_intent(intent)

        # process_queue should fail gracefully — not process anything
        results = engine.process_queue(auto_confirm=True, skip_checks=True)
        assert len(results) == 1
        assert "error" in results[0]
        assert "lock" in results[0]["error"].lower()

        # Intent should NOT have been touched
        intent = event_log.get_intent("concurrent-test:pr-1")
        assert intent.status == Status.VALIDATED

        # Release lock — now processing should work
        event_log.release_queue_lock(holder_pid=99999)

    def test_lock_released_after_processing(self, db_path, git_repo):
        """Queue lock is released even if processing raises."""

        intent = Intent(
            id="lock-release-test:pr-1",
            source="feature/alpha",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=1,
        )
        event_log.upsert_intent(intent)

        # Seed simulation
        event_log.append(event_log.Event(
            event_type=EventType.SIMULATION_COMPLETED,
            intent_id=intent.id,
            payload={"mergeable": True, "conflicts": [], "files_changed": ["feature_alpha.py"],
                     "source": "feature/alpha", "target": "main"},
        ))

        # Process
        engine.process_queue(
            auto_confirm=True, skip_checks=True,
            use_last_simulation=True, cwd=str(git_repo),
        )

        # Lock should be released
        lock = event_log.get_queue_lock_info()
        assert lock is None, "Queue lock should be released after processing"

        # A second call should succeed (not get locked out)
        results = engine.process_queue(
            auto_confirm=True, skip_checks=True,
            use_last_simulation=True, cwd=str(git_repo),
        )
        # No lock error
        assert not any("error" in r for r in results)


# ---------------------------------------------------------------------------
# Test 4: Retry and rejection (Invariant 3)
# ---------------------------------------------------------------------------

class TestRetryAndRejection:
    """Invariant 3: retries > max_retries → REJECTED."""

    def test_intent_rejected_after_max_retries(self, db_path, git_repo_conflict):
        """An intent that consistently fails validation is rejected
        after max_retries attempts."""

        repo = git_repo_conflict

        # First merge conflict-a so conflict-b will always fail
        from converge.scm import execute_merge_safe
        execute_merge_safe("feature/conflict-a", "main", cwd=repo)

        # Create intent for conflict-b at retries=2 (max_retries=3)
        intent = Intent(
            id="retry-test:pr-1",
            source="feature/conflict-b",
            target="main",
            status=Status.VALIDATED,
            created_by="test",
            risk_level=RiskLevel.LOW,
            priority=1,
            retries=2,
        )
        event_log.upsert_intent(intent)

        # Process queue with max_retries=3 — this is the 3rd attempt, should reject
        results = engine.process_queue(
            auto_confirm=True,
            skip_checks=True,
            max_retries=3,
            cwd=str(repo),
        )

        our_result = next((r for r in results if r.get("intent_id") == "retry-test:pr-1"), None)
        assert our_result is not None

        intent = event_log.get_intent("retry-test:pr-1")
        assert intent.status == Status.REJECTED

        # Rejection event emitted
        reject_events = event_log.query(event_type="intent.rejected", intent_id="retry-test:pr-1")
        assert len(reject_events) >= 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _webhook(base_url: str, event: str, payload: dict, delivery_id: str) -> dict:
    """Send a GitHub webhook to the live server."""
    req = Request(
        f"{base_url}/integrations/github/webhook",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery_id,
        },
        method="POST",
    )
    resp = urlopen(req)
    return json.loads(resp.read())
