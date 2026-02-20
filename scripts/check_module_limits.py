#!/usr/bin/env python3
"""Quality gate: enforce max LOC per module in src/converge/.

Usage:
    python scripts/check_module_limits.py [--max-loc 400] [--src src/converge]

Returns exit code 1 if any module exceeds the limit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def count_loc(path: Path) -> int:
    """Count non-blank, non-comment lines in a Python file."""
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


# Data-access adapters and engine are exempt — bulk SQL mapping code
# that cannot be meaningfully split without losing cohesion.
_EXEMPT = {
    "adapters/sqlite_store.py",
    "adapters/postgres_store.py",
    "engine.py",
}


def check_limits(
    src_dir: Path, max_loc: int, exempt: set[str] | None = None,
) -> list[tuple[str, int]]:
    """Return list of (relative_path, loc) for modules exceeding max_loc."""
    exempt = exempt or _EXEMPT
    violations = []
    for py_file in sorted(src_dir.rglob("*.py")):
        # Skip __pycache__
        if "__pycache__" in str(py_file):
            continue
        rel = str(py_file.relative_to(src_dir))
        if rel in exempt:
            continue
        loc = count_loc(py_file)
        if loc > max_loc:
            violations.append((rel, loc))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Check module LOC limits")
    parser.add_argument("--max-loc", type=int, default=400,
                        help="Maximum non-blank, non-comment lines per module (default: 400)")
    parser.add_argument("--src", default="src/converge",
                        help="Source directory to scan (default: src/converge)")
    args = parser.parse_args()

    src_dir = Path(args.src)
    if not src_dir.is_dir():
        print(f"ERROR: {src_dir} is not a directory", file=sys.stderr)
        return 1

    violations = check_limits(src_dir, args.max_loc)

    if violations:
        print(f"FAIL: {len(violations)} module(s) exceed {args.max_loc} LOC limit:")
        for path, loc in violations:
            print(f"  {path}: {loc} LOC (over by {loc - args.max_loc})")
        return 1

    # Print summary
    total_files = sum(1 for _ in src_dir.rglob("*.py") if "__pycache__" not in str(_))
    print(f"OK: All {total_files} modules are within {args.max_loc} LOC limit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
