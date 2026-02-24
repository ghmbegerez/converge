"""Security scanning orchestrator.

Coordinates multiple scanner adapters, persists findings, and emits events.
Used by the policy engine (AR-39) and API/CLI (AR-40).
"""

from __future__ import annotations

from typing import Any

from converge import event_log
from converge.event_types import EventType
from converge.models import Event, FindingSeverity, SecurityFinding, new_id, now_iso
from converge.ports import SecurityScannerPort


def run_scan(
    path: str,
    *,
    scanners: list[SecurityScannerPort] | None = None,
    intent_id: str | None = None,
    tenant_id: str | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Run all configured scanners against a path and persist findings.

    Returns a summary dict with total counts by severity, scanner results,
    and the scan_id for traceability.
    """
    if scanners is None:
        scanners = _default_scanners()

    scan_id = new_id()
    opts = {**options, "intent_id": intent_id, "tenant_id": tenant_id}

    # Emit scan started
    event_log.append(Event(
        event_type=EventType.SECURITY_SCAN_STARTED,
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload={"scan_id": scan_id, "scanners": [s.scanner_name for s in scanners], "path": path},
    ))

    all_findings: list[SecurityFinding] = []
    scanner_results: list[dict[str, Any]] = []

    for scanner in scanners:
        if not scanner.is_available():
            scanner_results.append({
                "scanner": scanner.scanner_name,
                "status": "skipped",
                "reason": "not installed",
                "findings": 0,
            })
            continue

        findings = scanner.scan(path, **opts)
        all_findings.extend(findings)
        scanner_results.append({
            "scanner": scanner.scanner_name,
            "status": "completed",
            "findings": len(findings),
        })

    # Persist findings (ensure scan-level context is attached)
    for f in all_findings:
        finding_dict = f.to_dict()
        finding_dict["scan_id"] = scan_id
        if intent_id:
            finding_dict["intent_id"] = intent_id
        if tenant_id:
            finding_dict["tenant_id"] = tenant_id
        event_log.upsert_security_finding(finding_dict)

    # Emit per-finding events for critical/high
    for f in all_findings:
        if f.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH):
            event_log.append(Event(
                event_type=EventType.SECURITY_FINDING_DETECTED,
                intent_id=intent_id,
                tenant_id=tenant_id,
                payload={"scan_id": scan_id, "finding": f.to_dict()},
            ))

    # Count by severity
    severity_counts: dict[str, int] = {}
    for f in all_findings:
        severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1

    summary = {
        "scan_id": scan_id,
        "total_findings": len(all_findings),
        "severity_counts": severity_counts,
        "scanners": scanner_results,
        "intent_id": intent_id,
        "tenant_id": tenant_id,
        "timestamp": now_iso(),
    }

    # Emit scan completed
    event_log.append(Event(
        event_type=EventType.SECURITY_SCAN_COMPLETED,
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload=summary,
    ))

    return summary


def scan_summary(
    *,
    intent_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Get a summary of security findings for dashboard display."""
    counts = event_log.count_security_findings(
        intent_id=intent_id, tenant_id=tenant_id,
    )
    scans = event_log.query(
        event_type=EventType.SECURITY_SCAN_COMPLETED,
        intent_id=intent_id,
        limit=5,
    )
    return {
        "finding_counts": counts,
        "recent_scans": scans,
    }


def _default_scanners() -> list[SecurityScannerPort]:
    """Load the default set of scanner adapters."""
    from converge.adapters.security import (
        BanditScanner,
        GitleaksScanner,
        PipAuditScanner,
    )
    return [BanditScanner(), PipAuditScanner(), GitleaksScanner()]
