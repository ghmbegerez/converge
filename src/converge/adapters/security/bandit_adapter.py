"""Bandit SAST scanner adapter.

Wraps the ``bandit`` CLI, parses JSON output, and normalizes findings
into SecurityFinding instances.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

from converge.models import FindingCategory, FindingSeverity, SecurityFinding, new_id

log = logging.getLogger(__name__)

_SEVERITY_MAP = {
    "HIGH": FindingSeverity.HIGH,
    "MEDIUM": FindingSeverity.MEDIUM,
    "LOW": FindingSeverity.LOW,
    "UNDEFINED": FindingSeverity.INFO,
}

_CONFIDENCE_MAP = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


class BanditScanner:
    scanner_name = "bandit"

    def is_available(self) -> bool:
        return shutil.which("bandit") is not None

    def scan(self, path: str, **options: Any) -> list[SecurityFinding]:
        if not self.is_available():
            log.warning("bandit not installed — skipping SAST scan")
            return []

        cmd = ["bandit", "-r", path, "-f", "json", "-q"]
        severity = options.get("severity")
        if severity:
            cmd.extend(["-l" * _severity_flag_count(severity)])
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=options.get("timeout", 120),
            )
        except subprocess.TimeoutExpired:
            log.error("bandit timed out scanning %s", path)
            return []
        except FileNotFoundError:
            log.warning("bandit binary not found")
            return []

        return _parse_output(result.stdout, options)


def _severity_flag_count(severity: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(severity, 1)


def _parse_output(raw: str, options: dict[str, Any]) -> list[SecurityFinding]:
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Failed to parse bandit JSON output")
        return []

    findings: list[SecurityFinding] = []
    for r in data.get("results", []):
        sev_str = r.get("issue_severity", "MEDIUM").upper()
        severity = _SEVERITY_MAP.get(sev_str, FindingSeverity.MEDIUM)
        conf_str = r.get("issue_confidence", "MEDIUM").upper()

        findings.append(SecurityFinding(
            id=new_id(),
            scanner="bandit",
            category=FindingCategory.SAST,
            severity=severity,
            file=r.get("filename", ""),
            line=r.get("line_number", 0),
            rule=r.get("test_id", ""),
            evidence=r.get("issue_text", ""),
            confidence=_CONFIDENCE_MAP.get(conf_str, "medium"),
            intent_id=options.get("intent_id"),
            tenant_id=options.get("tenant_id"),
        ))
    return findings
