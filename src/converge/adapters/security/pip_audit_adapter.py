"""pip-audit SCA scanner adapter.

Wraps the ``pip-audit`` CLI, parses JSON output, and normalizes findings
into SecurityFinding instances for dependency vulnerability detection.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

from converge.models import FindingCategory, FindingSeverity, SecurityFinding, new_id

log = logging.getLogger(__name__)

_CVSS_THRESHOLDS = [
    (9.0, FindingSeverity.CRITICAL),
    (7.0, FindingSeverity.HIGH),
    (4.0, FindingSeverity.MEDIUM),
    (0.1, FindingSeverity.LOW),
]


class PipAuditScanner:
    scanner_name = "pip-audit"

    def is_available(self) -> bool:
        return shutil.which("pip-audit") is not None

    def scan(self, path: str, **options: Any) -> list[SecurityFinding]:
        if not self.is_available():
            log.warning("pip-audit not installed — skipping SCA scan")
            return []

        cmd = ["pip-audit", "--format", "json", "--desc"]
        req_file = options.get("requirements_file")
        if req_file:
            cmd.extend(["-r", req_file])
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=options.get("timeout", 180),
                cwd=path,
            )
        except subprocess.TimeoutExpired:
            log.error("pip-audit timed out scanning %s", path)
            return []
        except FileNotFoundError:
            log.warning("pip-audit binary not found")
            return []

        return _parse_output(result.stdout, options)


def _parse_output(raw: str, options: dict[str, Any]) -> list[SecurityFinding]:
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Failed to parse pip-audit JSON output")
        return []

    findings: list[SecurityFinding] = []
    deps = data.get("dependencies", data) if isinstance(data, dict) else data
    if not isinstance(deps, list):
        return []

    for dep in deps:
        for vuln in dep.get("vulns", []):
            vuln_id = vuln.get("id", "")
            desc = vuln.get("description", "")
            fix = vuln.get("fix_versions", [])
            fix_str = f" (fix: {', '.join(fix)})" if fix else ""
            severity = _cvss_to_severity(vuln.get("cvss", 0.0))

            findings.append(SecurityFinding(
                id=new_id(),
                scanner="pip-audit",
                category=FindingCategory.SCA,
                severity=severity,
                file=f"dependency:{dep.get('name', '?')}=={dep.get('version', '?')}",
                rule=vuln_id,
                evidence=f"{desc}{fix_str}",
                confidence="high",
                intent_id=options.get("intent_id"),
                tenant_id=options.get("tenant_id"),
            ))
    return findings


def _cvss_to_severity(score: float) -> FindingSeverity:
    for threshold, severity in _CVSS_THRESHOLDS:
        if score >= threshold:
            return severity
    return FindingSeverity.INFO
