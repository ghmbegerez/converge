"""Tests for CLI dispatch, error handling, and argument parsing."""

import json
from io import StringIO
from unittest.mock import patch

import pytest

from converge.cli import _out, build_parser, main


class TestOutErrorHandling:
    """_out() returns exit code 1 when data contains 'error' key."""

    def test_out_success(self, db_path, capsys):
        code = _out({"ok": True})
        assert code == 0
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True

    def test_out_error_dict(self, db_path, capsys):
        code = _out({"error": "Something went wrong"})
        assert code == 1
        output = json.loads(capsys.readouterr().out)
        assert output["error"] == "Something went wrong"

    def test_out_list(self, db_path, capsys):
        code = _out([1, 2, 3])
        assert code == 0

    def test_out_error_in_nested_dict_no_false_positive(self, db_path, capsys):
        """Only top-level 'error' key triggers exit code 1."""
        code = _out({"data": {"error": "nested"}})
        assert code == 0


class TestParserStructure:
    """Parser builds correctly and rejects invalid input."""

    def test_parser_has_all_commands(self, db_path):
        parser = build_parser()
        # Verify no exception when parsing known commands
        args = parser.parse_args(["simulate", "--source", "a", "--target", "b"])
        assert args.command == "simulate"
        assert args.source == "a"

    def test_validate_requires_intent_id(self, db_path):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["validate"])

    def test_queue_run_defaults(self, db_path):
        parser = build_parser()
        args = parser.parse_args(["queue", "run"])
        assert args.command == "queue"
        assert args.limit == 20
        assert args.max_retries == 3
        assert args.auto_confirm is False

    def test_export_decisions_defaults(self, db_path):
        parser = build_parser()
        args = parser.parse_args(["export", "decisions"])
        assert args.format == "jsonl"


class TestMainDispatch:
    """main() dispatches to correct handler and returns proper exit codes."""

    def test_no_command_prints_help(self, db_path):
        code = main([])
        assert code == 1

    def test_unknown_subcommand(self, db_path):
        code = main(["queue"])
        assert code == 1

    def test_confirm_merge_not_found(self, db_path):
        code = main(["--db", str(db_path), "merge", "confirm",
                      "--intent-id", "nonexistent"])
        assert code == 1

    def test_validate_not_found(self, db_path):
        code = main(["--db", str(db_path), "validate",
                      "--intent-id", "nonexistent", "--skip-checks"])
        assert code == 1


class TestIntentCreateFromBranch:
    """--from-branch shortcut for intent creation."""

    def test_from_branch_creates_intent(self, db_path, capsys):
        from converge import event_log
        code = main(["--db", str(db_path), "intent", "create",
                      "--from-branch", "feature/login", "--target", "main"])
        assert code == 0
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True
        intent_id = output["intent_id"]

        intent = event_log.get_intent(intent_id)
        assert intent is not None
        assert intent.source == "feature/login"
        assert intent.target == "main"

    def test_from_branch_with_custom_id(self, db_path, capsys):
        from converge import event_log
        code = main(["--db", str(db_path), "intent", "create",
                      "--from-branch", "feature/x", "--intent-id", "my-custom-id"])
        assert code == 0
        output = json.loads(capsys.readouterr().out)
        assert output["intent_id"] == "my-custom-id"

        intent = event_log.get_intent("my-custom-id")
        assert intent is not None

    def test_from_branch_with_risk_level(self, db_path, capsys):
        from converge import event_log
        code = main(["--db", str(db_path), "intent", "create",
                      "--from-branch", "feature/risky",
                      "--risk-level", "high", "--priority", "1"])
        assert code == 0
        output = json.loads(capsys.readouterr().out)
        intent = event_log.get_intent(output["intent_id"])
        assert intent.risk_level.value == "high"
        assert intent.priority == 1

    def test_neither_file_nor_branch_errors(self, db_path, capsys):
        code = main(["--db", str(db_path), "intent", "create"])
        assert code == 1
        output = json.loads(capsys.readouterr().out)
        assert "error" in output
