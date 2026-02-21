"""S7 tests: modularization sanity checks, import validation, LOC limits."""

from __future__ import annotations

from pathlib import Path

import pytest


SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "converge"


# ---------------------------------------------------------------------------
# Import sanity: risk subpackage
# ---------------------------------------------------------------------------

class TestRiskPackageImports:
    def test_risk_top_level_reexports(self):
        """All public symbols accessible via converge.risk."""
        from converge.risk import (
            analyze_findings,
            build_dependency_graph,
            build_diagnostics,
            build_impact_edges,
            compute_complexity_delta,
            compute_contextual_value,
            compute_entropic_load,
            compute_path_dependence,
            containment_score,
            detect_bombs,
            evaluate_risk,
            graph_metrics,
            propagation_score,
        )
        # All should be callable
        assert callable(evaluate_risk)
        assert callable(build_dependency_graph)
        assert callable(detect_bombs)
        assert callable(compute_entropic_load)

    def test_risk_submodule_direct_import(self):
        """Submodules importable directly."""
        from converge.risk.signals import compute_entropic_load
        from converge.risk.graph import build_dependency_graph
        from converge.risk.bombs import detect_bombs
        from converge.risk.eval import evaluate_risk
        assert callable(compute_entropic_load)
        assert callable(build_dependency_graph)
        assert callable(detect_bombs)
        assert callable(evaluate_risk)

    def test_risk_constants_accessible(self):
        """Constants accessible from _constants module."""
        from converge.risk._constants import _RISK_BONUS, _CORE_TARGETS
        assert "main" in _CORE_TARGETS
        assert "medium" in _RISK_BONUS


# ---------------------------------------------------------------------------
# Import sanity: cli subpackage
# ---------------------------------------------------------------------------

class TestCLIPackageImports:
    def test_cli_top_level_exports(self):
        """build_parser, main, _out accessible from converge.cli."""
        from converge.cli import build_parser, main, _out
        assert callable(build_parser)
        assert callable(main)
        assert callable(_out)

    def test_cli_submodule_direct_import(self):
        """Submodules importable directly."""
        from converge.cli.intents import cmd_intent_create
        from converge.cli.queue import cmd_queue_run
        from converge.cli.risk_cmds import cmd_risk_eval
        from converge.cli.admin import cmd_serve
        assert callable(cmd_intent_create)
        assert callable(cmd_queue_run)
        assert callable(cmd_risk_eval)
        assert callable(cmd_serve)

    def test_cli_dispatch_has_all_commands(self):
        """Dispatch table covers all expected commands."""
        from converge.cli import _DISPATCH
        expected_keys = [
            ("intent", "create"), ("intent", "list"), ("intent", "status"),
            ("simulate", None), ("validate", None),
            ("merge", "confirm"),
            ("queue", "run"), ("queue", "reset"), ("queue", "inspect"),
            ("risk", "eval"), ("risk", "shadow"), ("risk", "gate"), ("risk", "review"),
            ("risk", "policy-set"), ("risk", "policy-get"),
            ("health", "now"), ("health", "trend"), ("health", "predict"),
            ("compliance", "report"), ("compliance", "alerts"),
            ("agent", "policy-set"), ("agent", "authorize"),
            ("audit", "prune"), ("audit", "events"),
            ("metrics", None), ("archaeology", None), ("predictions", None),
            ("export", "decisions"),
            ("serve", None), ("worker", None),
        ]
        for key in expected_keys:
            assert key in _DISPATCH, f"Missing dispatch key: {key}"

    def test_main_entry_point(self):
        """__main__.py references converge.cli.main."""
        import importlib
        spec = importlib.util.find_spec("converge.__main__")
        assert spec is not None, "converge.__main__ should be importable"
        source = Path(spec.origin).read_text()
        assert "from converge.cli import main" in source


# ---------------------------------------------------------------------------
# LOC limit validation
# ---------------------------------------------------------------------------

class TestModuleLimits:
    MAX_LOC = 400
    EXEMPT = {
        "adapters/base_store.py",
        "adapters/sqlite_store.py",
        "adapters/postgres_store.py",
        "engine.py",
    }

    def _count_loc(self, path: Path) -> int:
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                count += 1
        return count

    def test_no_module_exceeds_limit(self):
        """No non-exempt module exceeds 400 LOC."""
        violations = []
        for py_file in sorted(SRC_DIR.rglob("*.py")):
            if "__pycache__" in str(py_file):
                continue
            rel = str(py_file.relative_to(SRC_DIR))
            if rel in self.EXEMPT:
                continue
            loc = self._count_loc(py_file)
            if loc > self.MAX_LOC:
                violations.append(f"{rel}: {loc} LOC")
        assert violations == [], f"Modules over {self.MAX_LOC} LOC: {violations}"

    def test_risk_package_is_directory(self):
        """risk/ is a package, not a single file."""
        risk_dir = SRC_DIR / "risk"
        assert risk_dir.is_dir(), "risk should be a package directory"
        assert (risk_dir / "__init__.py").exists()
        assert not (SRC_DIR / "risk.py").exists(), "Old risk.py should not exist"

    def test_cli_package_is_directory(self):
        """cli/ is a package, not a single file."""
        cli_dir = SRC_DIR / "cli"
        assert cli_dir.is_dir(), "cli should be a package directory"
        assert (cli_dir / "__init__.py").exists()
        assert not (SRC_DIR / "cli.py").exists(), "Old cli.py should not exist"
