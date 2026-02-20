"""CLI commands: queue operations and merge confirm."""

from __future__ import annotations

import argparse

from converge.cli._helpers import _out


def cmd_queue_run(args: argparse.Namespace) -> int:
    from converge import engine
    results = engine.process_queue(
        args.db,
        limit=args.limit,
        target=args.target,
        auto_confirm=args.auto_confirm,
        max_retries=args.max_retries,
        use_last_simulation=args.use_last_simulation,
        skip_checks=args.skip_checks,
    )
    return _out(results)


def cmd_queue_reset(args: argparse.Namespace) -> int:
    from converge import engine
    result = engine.reset_queue(args.db, args.intent_id,
                                 set_status=getattr(args, "set_status", None),
                                 clear_lock=args.clear_lock)
    return _out(result)


def cmd_queue_inspect(args: argparse.Namespace) -> int:
    from converge import engine
    result = engine.inspect_queue(
        args.db,
        status=getattr(args, "status", None),
        min_retries=getattr(args, "min_retries", None),
        only_actionable=args.only_actionable,
        limit=args.limit,
    )
    return _out(result)


def cmd_merge_confirm(args: argparse.Namespace) -> int:
    from converge import engine
    result = engine.confirm_merge(args.db, args.intent_id, getattr(args, "merged_commit", None))
    return _out(result)
