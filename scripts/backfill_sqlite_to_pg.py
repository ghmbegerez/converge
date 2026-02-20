#!/usr/bin/env python3
"""Backfill data from SQLite to PostgreSQL.

Usage:
    python scripts/backfill_sqlite_to_pg.py --sqlite-path .converge/state.db --pg-dsn "postgresql://..."

Options:
    --dry-run     Print counts without writing.
    --verify      After backfill, compare row counts between backends.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

import psycopg
from psycopg.rows import dict_row


_TABLES = [
    ("events", ["id", "trace_id", "timestamp", "event_type", "intent_id",
                 "agent_id", "tenant_id", "payload", "evidence"]),
    ("intents", ["id", "source", "target", "status", "created_at", "created_by",
                  "risk_level", "priority", "semantic", "technical",
                  "checks_required", "dependencies", "retries", "tenant_id",
                  "updated_at"]),
    ("agent_policies", ["agent_id", "tenant_id", "data", "updated_at"]),
    ("compliance_thresholds", ["tenant_id", "data", "updated_at"]),
    ("risk_policies", ["tenant_id", "data", "version", "updated_at"]),
    ("queue_locks", ["lock_name", "holder_pid", "acquired_at", "expires_at"]),
    ("webhook_deliveries", ["delivery_id", "received_at"]),
]


def _count(conn, table: str) -> int:
    if hasattr(conn, "row_factory"):
        # psycopg
        row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()
        return row["cnt"] if isinstance(row, dict) else row[0]
    else:
        # sqlite3
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def backfill(sqlite_path: str, pg_dsn: str, *, dry_run: bool = False) -> dict[str, int]:
    """Copy all rows from SQLite to Postgres.  Returns per-table counts."""
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = psycopg.connect(pg_dsn, row_factory=dict_row)

    counts: dict[str, int] = {}

    for table, columns in _TABLES:
        rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
        counts[table] = len(rows)

        if dry_run:
            print(f"  {table}: {len(rows)} rows (dry run)")
            continue

        if not rows:
            print(f"  {table}: 0 rows (skip)")
            continue

        placeholders = ", ".join(["%s"] * len(columns))
        col_list = ", ".join(columns)
        conflict_col = columns[0]
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_col}) DO NOTHING"
        )

        batch = []
        for r in rows:
            row_dict = dict(r)
            batch.append(tuple(row_dict.get(c) for c in columns))

        with pg_conn.cursor() as cur:
            cur.executemany(sql, batch)
        pg_conn.commit()
        print(f"  {table}: {len(rows)} rows copied")

    sqlite_conn.close()
    pg_conn.close()
    return counts


def verify(sqlite_path: str, pg_dsn: str) -> bool:
    """Compare row counts between SQLite and Postgres."""
    sqlite_conn = sqlite3.connect(sqlite_path)
    pg_conn = psycopg.connect(pg_dsn, row_factory=dict_row)

    ok = True
    for table, _ in _TABLES:
        sq_count = _count(sqlite_conn, table)
        pg_count = _count(pg_conn, table)
        status = "OK" if sq_count == pg_count else "MISMATCH"
        if status == "MISMATCH":
            ok = False
        print(f"  {table}: sqlite={sq_count}  postgres={pg_count}  [{status}]")

    sqlite_conn.close()
    pg_conn.close()
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill SQLite -> Postgres")
    parser.add_argument("--sqlite-path", required=True, help="Path to SQLite DB")
    parser.add_argument("--pg-dsn", required=True, help="PostgreSQL DSN")
    parser.add_argument("--dry-run", action="store_true", help="Print counts only")
    parser.add_argument("--verify", action="store_true", help="Verify parity after backfill")
    args = parser.parse_args()

    print("Backfilling...")
    backfill(args.sqlite_path, args.pg_dsn, dry_run=args.dry_run)

    if args.verify and not args.dry_run:
        print("\nVerifying parity...")
        if verify(args.sqlite_path, args.pg_dsn):
            print("All tables match.")
        else:
            print("MISMATCH detected!", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
