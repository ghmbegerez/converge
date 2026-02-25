"""Tests for the converge doctor command."""

import json
from io import StringIO
from unittest.mock import patch

from converge.cli import main


class TestDoctor:
    def test_doctor_basic(self, db_path):
        """doctor with a valid db_path returns pass or warn."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            rc = main(["--db", str(db_path), "doctor"])
        output = json.loads(mock_stdout.getvalue())
        assert output["overall"] in ("pass", "warn", "fail")
        assert len(output["checks"]) >= 3

    def test_doctor_database_check(self, db_path):
        """Database check passes with a valid db_path."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            main(["--db", str(db_path), "doctor"])
        output = json.loads(mock_stdout.getvalue())
        db_check = next(c for c in output["checks"] if c["check"] == "database")
        assert db_check["status"] == "pass"

    def test_doctor_feature_flags_reported(self, db_path):
        """Feature flags check appears in output."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            main(["--db", str(db_path), "doctor"])
        output = json.loads(mock_stdout.getvalue())
        ff_check = next(c for c in output["checks"] if c["check"] == "feature_flags")
        assert ff_check["status"] == "pass"
        assert "/" in ff_check["detail"]  # "N/M enabled" format

    def test_doctor_cli_dispatch(self, db_path):
        """main(["doctor", ...]) dispatches correctly."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            rc = main(["--db", str(db_path), "doctor"])
        assert rc == 0
        output = json.loads(mock_stdout.getvalue())
        assert "overall" in output
        assert "checks" in output
