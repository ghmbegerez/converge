"""Code-area ownership and separation of duties (AR-45).

Maps path patterns to owners/teams and enforces that agents cannot approve
changes in code areas they own (separation of duties).
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from converge import event_log
from converge.event_types import EventType
from converge.models import Event


@dataclass
class OwnershipRule:
    pattern: str                     # glob pattern, e.g. "src/auth/**"
    owners: list[str]                # owner identifiers (agent_id, team, user)
    team: str = ""                   # optional team label


@dataclass
class OwnershipConfig:
    rules: list[OwnershipRule] = field(default_factory=list)
    strict: bool = False             # if True, missing ownership blocks

    def owners_for(self, file_path: str) -> list[str]:
        """Return all owners whose pattern matches the file path."""
        owners: list[str] = []
        for rule in self.rules:
            if fnmatch.fnmatch(file_path, rule.pattern):
                owners.extend(rule.owners)
        return owners

    def is_owner(self, agent_id: str, files: list[str]) -> bool:
        """Check if agent_id is an owner of any of the files."""
        for f in files:
            if agent_id in self.owners_for(f):
                return True
        return False


def load_ownership(config_path: str | Path | None = None) -> OwnershipConfig:
    """Load ownership config from JSON file."""
    paths_to_try: list[Path] = []
    if config_path:
        paths_to_try.append(Path(config_path))
    paths_to_try.extend([
        Path(".converge/ownership.json"),
        Path("ownership.json"),
    ])

    for p in paths_to_try:
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            rules = [
                OwnershipRule(
                    pattern=r["pattern"],
                    owners=r.get("owners", []),
                    team=r.get("team", ""),
                )
                for r in data.get("rules", [])
            ]
            return OwnershipConfig(
                rules=rules,
                strict=data.get("strict", False),
            )

    return OwnershipConfig()


def check_sod(
    *,
    agent_id: str,
    files: list[str],
    action: str = "approve",
    config: OwnershipConfig | None = None,
) -> dict[str, Any]:
    """Check separation of duties: agent cannot approve their own code area.

    Returns a dict with 'allowed' bool and details.
    """
    cfg = config or load_ownership()

    if not cfg.rules:
        # No ownership rules → permissive by default
        return {"allowed": True, "reason": "no ownership rules configured"}

    is_owner = cfg.is_owner(agent_id, files)

    if is_owner and action in ("approve", "merge"):
        # SoD violation: agent owns the code and is trying to approve/merge
        event_log.append(Event(
            event_type=EventType.SOD_VIOLATION,
            agent_id=agent_id,
            payload={
                "agent_id": agent_id,
                "action": action,
                "files": files[:20],  # limit payload size
                "reason": "agent is owner of touched code area",
            },
        ))
        return {
            "allowed": False,
            "reason": f"SoD violation: {agent_id} owns code in touched files",
            "owned_files": [f for f in files if agent_id in cfg.owners_for(f)],
        }

    return {"allowed": True, "reason": "no SoD conflict"}


def ownership_summary(
    files: list[str],
    config: OwnershipConfig | None = None,
) -> dict[str, Any]:
    """Return ownership mapping for a list of files."""
    cfg = config or load_ownership()
    mapping: dict[str, list[str]] = {}
    unowned: list[str] = []
    for f in files:
        owners = cfg.owners_for(f)
        if owners:
            mapping[f] = owners
        else:
            unowned.append(f)

    return {
        "owned": mapping,
        "unowned": unowned,
        "coverage": len(mapping) / max(len(files), 1),
    }
