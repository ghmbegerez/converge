"""Tests for converge.scm: git operations, simulate_merge, log_entries."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from converge.models import Simulation
from converge.scm import (
    branch_exists,
    current_head,
    git,
    log_entries,
    repo_root,
    run,
    simulate_merge,
)


class TestRunAndGit:
    def test_run_delegates_to_subprocess(self):
        result = run(["echo", "hello"])
        assert result.stdout.strip() == "hello"

    def test_run_raises_on_failure(self):
        with pytest.raises(subprocess.CalledProcessError):
            run(["false"])

    def test_run_no_check(self):
        result = run(["false"], check=False)
        assert result.returncode != 0

    def test_git_calls_git_binary(self):
        result = git("version", check=True)
        assert "git version" in result.stdout


class TestRepoRoot:
    @patch("converge.scm.git")
    def test_repo_root_returns_path(self, mock_git):
        mock_git.return_value = MagicMock(stdout="/home/user/repo\n")
        root = repo_root()
        assert root == Path("/home/user/repo")
        mock_git.assert_called_once_with("rev-parse", "--show-toplevel", cwd=None)


class TestSimulateMerge:
    @patch("converge.scm.repo_root")
    @patch("converge.scm.git")
    def test_mergeable_simulation(self, mock_git, mock_root):
        mock_root.return_value = Path("/repo")
        # merge-tree succeeds
        merge_result = MagicMock(returncode=0)
        # diff-tree returns files
        diff_result = MagicMock(stdout="src/a.py\nsrc/b.py\n")
        mock_git.side_effect = [merge_result, diff_result]

        sim = simulate_merge("feature/x", "main")

        assert isinstance(sim, Simulation)
        assert sim.mergeable is True
        assert sim.conflicts == []
        assert sim.files_changed == ["src/a.py", "src/b.py"]

    @patch("converge.scm.repo_root")
    @patch("converge.scm.git")
    def test_conflicting_simulation(self, mock_git, mock_root):
        mock_root.return_value = Path("/repo")
        merge_result = MagicMock(returncode=1, stderr="CONFLICT (content): Merge conflict in src/c.py", stdout="")
        diff_result = MagicMock(stdout="src/c.py\n")
        mock_git.side_effect = [merge_result, diff_result]

        sim = simulate_merge("feature/y", "main")

        assert sim.mergeable is False
        assert "src/c.py" in sim.conflicts

    @patch("converge.scm.repo_root")
    @patch("converge.scm.git")
    def test_conflict_fallback_parsing(self, mock_git, mock_root):
        mock_root.return_value = Path("/repo")
        merge_result = MagicMock(returncode=1, stderr="", stdout="100644\tfile1.py\n100644\tfile2.py\n")
        diff_result = MagicMock(stdout="file1.py\nfile2.py\n")
        mock_git.side_effect = [merge_result, diff_result]

        sim = simulate_merge("feature/z", "main")

        assert sim.mergeable is False
        assert "file1.py" in sim.conflicts


class TestBranchExists:
    @patch("converge.scm.git")
    def test_existing_branch(self, mock_git):
        mock_git.return_value = MagicMock(returncode=0)
        assert branch_exists("main") is True

    @patch("converge.scm.git")
    def test_nonexistent_branch(self, mock_git):
        mock_git.return_value = MagicMock(returncode=128)
        assert branch_exists("nonexistent") is False


class TestCurrentHead:
    @patch("converge.scm.git")
    def test_current_head_returns_sha(self, mock_git):
        mock_git.return_value = MagicMock(stdout="abc123def456\n")
        sha = current_head()
        assert sha == "abc123def456"


class TestLogEntries:
    @patch("converge.scm.git")
    def test_log_entries_parses_output(self, mock_git):
        sep = "---CONVERGE_ENTRY---"
        output = (
            f"{sep}\n"
            "abc123\n"
            "Alice\n"
            "2025-01-15T10:00:00Z\n"
            "Fix login bug\n"
            "src/auth.py\n"
            "src/login.py\n"
            f"{sep}\n"
            "def456\n"
            "Bob\n"
            "2025-01-14T09:00:00Z\n"
            "Add tests\n"
            "tests/test_auth.py\n"
        )
        mock_git.return_value = MagicMock(returncode=0, stdout=output)

        entries = log_entries(max_commits=100)

        assert len(entries) == 2
        assert entries[0]["sha"] == "abc123"
        assert entries[0]["author"] == "Alice"
        assert entries[0]["files"] == ["src/auth.py", "src/login.py"]
        assert entries[1]["sha"] == "def456"
        assert entries[1]["files"] == ["tests/test_auth.py"]

    @patch("converge.scm.git")
    def test_log_entries_empty_on_error(self, mock_git):
        mock_git.return_value = MagicMock(returncode=128, stdout="")
        entries = log_entries()
        assert entries == []

    @patch("converge.scm.git")
    def test_log_entries_skips_incomplete_blocks(self, mock_git):
        sep = "---CONVERGE_ENTRY---"
        output = f"{sep}\nabc\n"  # Only 1 line, not enough
        mock_git.return_value = MagicMock(returncode=0, stdout=output)
        entries = log_entries()
        assert entries == []
