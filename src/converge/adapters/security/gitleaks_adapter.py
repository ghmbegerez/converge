"""Gitleaks secrets scanner adapter.

Wraps the ``gitleaks`` CLI, parses JSON output, and normalizes findings
into SecurityFinding instances for secrets detection.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

from converge.models import FindingCategory, FindingSeverity, SecurityFinding, new_id

log = logging.getLogger(__name__)


class GitleaksScanner:
    scanner_name = "gitleaks"

    def is_available(self) -> bool:
        return shutil.which("gitleaks") is not None

    def scan(self, path: str, **options: Any) -> list[SecurityFinding]:
        if not self.is_available():
            log.warning("gitleaks not installed — skipping secrets scan")
            return []

        cmd = [
            "gitleaks", "detect",
            "--source", path,
            "--report-format", "json",
            "--report-path", "/dev/stdout",
            "--no-git",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=options.get("timeout", 120),
            )
        except subprocess.TimeoutExpired:
            log.error("gitleaks timed out scanning %s", path)
            return []
        except FileNotFoundError:
            log.warning("gitleaks binary not found")
            return []

        return _parse_output(result.stdout, options)


def _parse_output(raw: str, options: dict[str, Any]) -> list[SecurityFinding]:
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Failed to parse gitleaks JSON output")
        return []

    if not isinstance(data, list):
        return []

    findings: list[SecurityFinding] = []
    for leak in data:
        # Secrets are always high severity
        rule_id = leak.get("RuleID", leak.get("ruleID", ""))
        file_path = leak.get("File", leak.get("file", ""))
        line = leak.get("StartLine", leak.get("startLine", 0))
        match = leak.get("Match", leak.get("match", ""))
        # Redact the actual secret in evidence
        evidence = f"Rule: {rule_id}"
        if match:
            evidence += f" | Match: {match[:8]}{'*' * max(0, len(match) - 8)}"

        findings.append(SecurityFinding(
            id=new_id(),
            scanner="gitleaks",
            category=FindingCategory.SECRETS,
            severity=FindingSeverity.HIGH,
            file=file_path,
            line=line,
            rule=rule_id,
            evidence=evidence,
            confidence="high",
            intent_id=options.get("intent_id"),
            tenant_id=options.get("tenant_id"),
        ))
    return findings
