"""Git operations. Merge simulation uses git merge-tree (no disk I/O, no locks)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from converge.models import Simulation, now_iso


def run(cmd: list[str], cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def git(*args: str, cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=cwd, check=check)


def repo_root(cwd: str | Path | None = None) -> Path:
    r = git("rev-parse", "--show-toplevel", cwd=cwd)
    return Path(r.stdout.strip())


def simulate_merge(source: str, target: str, cwd: str | Path | None = None) -> Simulation:
    """Simulate a merge using git merge-tree. No working directory, no locks, no disk I/O."""
    root = repo_root(cwd)
    result = git("merge-tree", "--write-tree", target, source, cwd=root, check=False)

    # Files changed between branches
    diff = git("diff-tree", "--no-commit-id", "--name-only", "-r", target, source, cwd=root, check=False)
    files = [f for f in diff.stdout.strip().splitlines() if f]

    if result.returncode == 0:
        return Simulation(
            mergeable=True,
            conflicts=[],
            files_changed=files,
            timestamp=now_iso(),
            source=source,
            target=target,
        )

    # Parse conflicts from merge-tree output
    conflicts = re.findall(r"CONFLICT.*?:\s.*?in\s+(\S+)", result.stderr)
    if not conflicts:
        # Fallback: parse file entries with conflict markers (mode 1/2/3 lines)
        conflict_files = set()
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                conflict_files.add(parts[1])
        conflicts = sorted(conflict_files)

    return Simulation(
        mergeable=False,
        conflicts=conflicts,
        files_changed=files,
        timestamp=now_iso(),
        source=source,
        target=target,
    )


def execute_merge(source: str, target: str, cwd: str | Path | None = None) -> str:
    """Execute a real merge (ff or no-ff). Returns the merge commit SHA.

    .. deprecated:: Use execute_merge_safe() instead — it isolates the merge
       in a git worktree so the main working directory is never modified.
    """
    root = repo_root(cwd)
    git("checkout", target, cwd=root)
    git("merge", "--no-ff", source, "-m", f"converge: merge {source} into {target}", cwd=root)
    result = git("rev-parse", "HEAD", cwd=root)
    return result.stdout.strip()


def execute_merge_safe(source: str, target: str, cwd: str | Path | None = None) -> str:
    """Execute a merge in an isolated git worktree. Returns the merge commit SHA.

    The main working directory is never modified. Uses a detached worktree so
    the target branch does not need to be free (it may already be checked out in
    the main worktree). On success the target ref is updated to the merge commit.
    On failure the worktree is cleaned up and the exception propagates.
    """
    root = repo_root(cwd)
    worktree_dir = tempfile.mkdtemp(prefix="converge-merge-")
    try:
        # Create an isolated worktree at target's HEAD (detached)
        git("worktree", "add", "--detach", worktree_dir, target, cwd=root)

        # Perform the merge inside the worktree
        git(
            "merge", "--no-ff", source,
            "-m", f"converge: merge {source} into {target}",
            cwd=worktree_dir,
        )

        # Extract the SHA of the merge commit
        result = git("rev-parse", "HEAD", cwd=worktree_dir)
        sha = result.stdout.strip()

        # Update the target branch ref to point to the merge commit
        git("update-ref", f"refs/heads/{target}", sha, cwd=root)

        return sha

    finally:
        # Always clean up the worktree
        try:
            git("worktree", "remove", "--force", worktree_dir, cwd=root)
        except Exception:
            # Fallback: remove the directory directly
            shutil.rmtree(worktree_dir, ignore_errors=True)
            try:
                git("worktree", "prune", cwd=root)
            except Exception:
                pass


def current_head(cwd: str | Path | None = None) -> str:
    r = git("rev-parse", "HEAD", cwd=cwd)
    return r.stdout.strip()


def branch_exists(branch: str, cwd: str | Path | None = None) -> bool:
    r = git("rev-parse", "--verify", branch, cwd=cwd, check=False)
    return r.returncode == 0


def log_entries(max_commits: int = 400, cwd: str | Path | None = None) -> list[dict]:
    """Return git log as list of dicts for archaeology."""
    sep = "---CONVERGE_ENTRY---"
    fmt = f"{sep}%n%H%n%an%n%aI%n%s"
    r = git("log", f"--max-count={max_commits}", f"--format={fmt}", "--name-only", cwd=cwd, check=False)
    if r.returncode != 0:
        return []
    entries = []
    blocks = r.stdout.split(sep)
    for block in blocks:
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 4:
            continue
        sha, author, date, subject = lines[0], lines[1], lines[2], lines[3]
        files = [f for f in lines[4:] if f.strip() and not f.startswith("Merge")]
        entries.append({"sha": sha, "author": author, "date": date, "subject": subject, "files": files})
    return entries
