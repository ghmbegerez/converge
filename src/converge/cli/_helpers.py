"""Shared CLI helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _default_db() -> str:
    return str(Path(".converge") / "state.db")


def _out(data: Any) -> int:
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    if isinstance(data, dict) and "error" in data:
        return 1
    return 0
