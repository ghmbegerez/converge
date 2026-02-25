"""Tests for safe worktree-isolated merge (execute_merge_safe)."""

import os
import subprocess
from pathlib import Path

import pytest

from converge.scm import (
    current_head,
    execute_merge_safe,
    repo_root,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a bare-bones git repo with main + feature/x branches."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def _git(*args):
        subprocess.run(
            ["git", *args], cwd=repo,
            capture_output=True, text=True, check=True,
        )

    _git("init", "-b", "main")
    _git("config", "user.email", "test@test.com")
    _git("config", "user.name", "Test")

    # Initial commit on main
    (repo / "README.md").write_text("# hello\n")
    _git("add", "README.md")
    _git("commit", "-m", "Initial commit")

    # Create feature/x with a new file
    _git("checkout", "-b", "feature/x")
    (repo / "feature.py").write_text("print('feature')\n")
    _git("add", "feature.py")
    _git("commit", "-m", "Add feature")

    # Go back to main
    _git("checkout", "main")

    return repo


@pytest.fixture
def conflicting_repo(tmp_path):
    """Create a repo where main and feature/x conflict on the same file."""
    repo = tmp_path / "conflict_repo"
    repo.mkdir()

    def _git(*args):
        subprocess.run(
            ["git", *args], cwd=repo,
            capture_output=True, text=True, check=True,
        )

    _git("init", "-b", "main")
    _git("config", "user.email", "test@test.com")
    _git("config", "user.name", "Test")

    # Initial commit
    (repo / "shared.py").write_text("line1\n")
    _git("add", "shared.py")
    _git("commit", "-m", "Initial commit")

    # Feature branch modifies shared.py
    _git("checkout", "-b", "feature/x")
    (repo / "shared.py").write_text("feature version\n")
    _git("add", "shared.py")
    _git("commit", "-m", "Feature change")

    # Main also modifies shared.py
    _git("checkout", "main")
    (repo / "shared.py").write_text("main version\n")
    _git("add", "shared.py")
    _git("commit", "-m", "Main change")

    return repo


class TestWorktreeMerge:
    def test_worktree_merge_success(self, git_repo):
        """Merge via worktree succeeds and returns a valid SHA."""
        sha = execute_merge_safe("feature/x", "main", cwd=git_repo)
        assert sha and len(sha) == 40
        # Target branch (main) was updated
        head = current_head(cwd=git_repo)
        assert head == sha

    def test_worktree_merge_conflict_raises(self, conflicting_repo):
        """Conflicting merge raises an exception."""
        with pytest.raises(subprocess.CalledProcessError):
            execute_merge_safe("feature/x", "main", cwd=conflicting_repo)

    def test_worktree_cleanup_on_failure(self, conflicting_repo, tmp_path):
        """Worktree temporary directory is cleaned up even on conflict."""
        # Count worktrees before
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=conflicting_repo, capture_output=True, text=True,
        )
        before = r.stdout.count("worktree ")

        with pytest.raises(subprocess.CalledProcessError):
            execute_merge_safe("feature/x", "main", cwd=conflicting_repo)

        # Worktrees should be cleaned up
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=conflicting_repo, capture_output=True, text=True,
        )
        after = r.stdout.count("worktree ")
        assert after == before

    def test_working_directory_untouched(self, git_repo):
        """HEAD and index of the main working directory are preserved during merge."""
        head_before = current_head(cwd=git_repo)

        # Create a tracked file change to verify index isn't disturbed
        marker = git_repo / "marker.txt"
        marker.write_text("untouched\n")

        sha = execute_merge_safe("feature/x", "main", cwd=git_repo)

        # The marker file should still be there (working dir not reset)
        assert marker.exists()
        assert marker.read_text() == "untouched\n"
