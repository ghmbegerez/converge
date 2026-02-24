#!/usr/bin/env python3
"""Backfill intent_commit_links from legacy technical metadata (AR-06).

Reads all intents and creates commit links from:
  - technical.initial_base_commit → role=head
  - technical.merge_commit_sha → role=merge (if present)

Idempotent: re-running produces the same result (upsert).

Usage:
  PYTHONPATH=src python3 scripts/backfill_commit_links.py [--db PATH]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from converge import event_log
from converge.models import now_iso


def backfill(db_path: str) -> dict[str, int]:
    intents = event_log.list_intents(db_path, limit=10000)
    stats = {"total": len(intents), "linked": 0, "skipped": 0}

    for intent in intents:
        tech = intent.technical
        repo = tech.get("repo", "")
        head_sha = tech.get("initial_base_commit", "")
        merge_sha = tech.get("merge_commit_sha", "")

        if not head_sha:
            stats["skipped"] += 1
            continue

        event_log.upsert_commit_link(
            db_path, intent.id, repo, head_sha, "head", now_iso(),
        )
        if merge_sha:
            event_log.upsert_commit_link(
                db_path, intent.id, repo, merge_sha, "merge", now_iso(),
            )
        stats["linked"] += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill commit links from technical metadata")
    parser.add_argument("--db", default=os.environ.get("CONVERGE_DB_PATH", ".converge/state.db"))
    args = parser.parse_args()

    stats = backfill(args.db)
    print(f"Backfill complete: {stats}")


if __name__ == "__main__":
    main()
