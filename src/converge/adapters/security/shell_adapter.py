"""Shell command scanner adapter (legacy fallback).

Runs a configurable shell command (default: ``make security-scan``) and
parses structured JSON output if available, or treats non-zero exit as
a single warning finding.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from converge.models import FindingCategory, FindingSeverity, SecurityFinding, new_id

log = logging.getLogger(__name__)


class ShellScanner:
    scanner_name = "shell"

    def __init__(self, command: str = "make security-scan"):
        self._command = command

    def is_available(self) -> bool:
        return True  # shell is always available

    def scan(self, path: str, **options: Any) -> list[SecurityFinding]:
        cmd = options.get("command", self._command)
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=options.get("timeout", 120),
                cwd=path,
            )
        except subprocess.TimeoutExpired:
            log.error("Shell scanner timed out: %s", cmd)
            return []

        # Try JSON parsing first
        findings = _try_parse_json(result.stdout, options)
        if findings is not None:
            return findings

        # Non-zero exit without JSON → single warning finding
        if result.returncode != 0:
            return [SecurityFinding(
                id=new_id(),
                scanner="shell",
                category=FindingCategory.SAST,
                severity=FindingSeverity.MEDIUM,
                evidence=f"Command '{cmd}' exited with code {result.returncode}. "
                         f"stderr: {result.stderr[:500]}",
                confidence="low",
                intent_id=options.get("intent_id"),
                tenant_id=options.get("tenant_id"),
            )]

        return []


def _try_parse_json(
    raw: str, options: dict[str, Any],
) -> list[SecurityFinding] | None:
    """Try to parse JSON output from the shell command."""
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    items = data if isinstance(data, list) else data.get("findings", [])
    if not isinstance(items, list):
        return None

    findings: list[SecurityFinding] = []
    for item in items:
        sev = item.get("severity", "medium").lower()
        severity = getattr(FindingSeverity, sev.upper(), FindingSeverity.MEDIUM)
        cat = item.get("category", "sast").lower()
        category = getattr(FindingCategory, cat.upper(), FindingCategory.SAST)

        findings.append(SecurityFinding(
            id=new_id(),
            scanner="shell",
            category=category,
            severity=severity,
            file=item.get("file", ""),
            line=item.get("line", 0),
            rule=item.get("rule", ""),
            evidence=item.get("evidence", item.get("message", "")),
            confidence=item.get("confidence", "medium"),
            intent_id=options.get("intent_id"),
            tenant_id=options.get("tenant_id"),
        ))
    return findings
