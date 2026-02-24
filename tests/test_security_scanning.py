"""Tests for security scanning (AR-37..AR-40)."""

import json
from unittest.mock import MagicMock, patch

from converge import event_log, security
from converge.adapters.security import (
    BanditScanner,
    GitleaksScanner,
    PipAuditScanner,
    ShellScanner,
)
from converge.event_types import EventType
from converge.models import (
    Event,
    FindingCategory,
    FindingSeverity,
    GateName,
    SecurityFinding,
    new_id,
    RiskLevel,
)
from converge.policy import PolicyConfig, evaluate



class TestSecurityFinding:
    """SecurityFinding data model."""

    def test_create_finding(self, db_path):
        f = SecurityFinding(
            id="f-1", scanner="bandit", category=FindingCategory.SAST,
            severity=FindingSeverity.HIGH, file="src/app.py", line=42,
            rule="B101", evidence="assert used",
        )
        assert f.scanner == "bandit"
        assert f.severity == FindingSeverity.HIGH

    def test_to_dict(self, db_path):
        f = SecurityFinding(
            id="f-1", scanner="bandit", category=FindingCategory.SAST,
            severity=FindingSeverity.CRITICAL,
        )
        d = f.to_dict()
        assert d["severity"] == "critical"
        assert d["category"] == "sast"
        assert d["scanner"] == "bandit"

    def test_finding_enums(self, db_path):
        assert FindingSeverity.CRITICAL.value == "critical"
        assert FindingCategory.SECRETS.value == "secrets"

    def test_gate_name_security(self, db_path):
        assert GateName.SECURITY.value == "security"


class TestSecurityEventTypes:
    """Event types for security scanning."""

    def test_event_types_exist(self, db_path):
        assert EventType.SECURITY_SCAN_STARTED == "security.scan.started"
        assert EventType.SECURITY_SCAN_COMPLETED == "security.scan.completed"
        assert EventType.SECURITY_FINDING_DETECTED == "security.finding.detected"


class TestSecurityFindingStore:
    """Security findings persistence."""

    def test_upsert_and_list(self, db_path):
        finding = {
            "id": "f-1", "scanner": "bandit", "category": "sast",
            "severity": "high", "file": "app.py", "line": 10,
            "rule": "B101", "evidence": "assert used",
            "confidence": "high", "intent_id": "i-1", "tenant_id": "t-1",
        }
        event_log.upsert_security_finding(finding)
        results = event_log.list_security_findings(intent_id="i-1")
        assert len(results) == 1
        assert results[0]["scanner"] == "bandit"
        assert results[0]["severity"] == "high"

    def test_filter_by_severity(self, db_path):
        for i, sev in enumerate(["critical", "high", "medium", "low"]):
            event_log.upsert_security_finding({
                "id": f"f-{i}", "scanner": "bandit", "category": "sast",
                "severity": sev, "intent_id": "i-1",
            })
        results = event_log.list_security_findings(severity="critical")
        assert len(results) == 1

    def test_filter_by_scanner(self, db_path):
        for scanner in ["bandit", "gitleaks", "pip-audit"]:
            event_log.upsert_security_finding({
                "id": f"f-{scanner}", "scanner": scanner,
                "category": "sast", "severity": "medium",
            })
        results = event_log.list_security_findings(scanner="gitleaks")
        assert len(results) == 1

    def test_count_by_severity(self, db_path):
        for i in range(3):
            event_log.upsert_security_finding({
                "id": f"c-{i}", "scanner": "bandit",
                "category": "sast", "severity": "critical",
            })
        for i in range(2):
            event_log.upsert_security_finding({
                "id": f"h-{i}", "scanner": "bandit",
                "category": "sast", "severity": "high",
            })
        counts = event_log.count_security_findings()
        assert counts["critical"] == 3
        assert counts["high"] == 2
        assert counts["total"] == 5

    def test_tenant_isolation(self, db_path):
        event_log.upsert_security_finding({
            "id": "f-a", "scanner": "bandit", "category": "sast",
            "severity": "high", "tenant_id": "t-A",
        })
        event_log.upsert_security_finding({
            "id": "f-b", "scanner": "bandit", "category": "sast",
            "severity": "high", "tenant_id": "t-B",
        })
        assert len(event_log.list_security_findings(tenant_id="t-A")) == 1
        assert len(event_log.list_security_findings(tenant_id="t-B")) == 1


# ---------------------------------------------------------------------------
# AR-38: Scanner adapters
# ---------------------------------------------------------------------------

class TestBanditAdapter:
    """Bandit SAST scanner adapter."""

    def test_is_available_check(self, db_path):
        scanner = BanditScanner()
        # Just verify the method exists and returns bool
        assert isinstance(scanner.is_available(), bool)

    def test_scanner_name(self, db_path):
        assert BanditScanner().scanner_name == "bandit"

    def test_parse_bandit_output(self, db_path):
        from converge.adapters.security.bandit_adapter import _parse_output
        raw = json.dumps({
            "results": [
                {
                    "filename": "app.py",
                    "line_number": 42,
                    "test_id": "B101",
                    "issue_text": "Use of assert detected.",
                    "issue_severity": "MEDIUM",
                    "issue_confidence": "HIGH",
                },
                {
                    "filename": "utils.py",
                    "line_number": 10,
                    "test_id": "B301",
                    "issue_text": "Use of insecure hash.",
                    "issue_severity": "HIGH",
                    "issue_confidence": "MEDIUM",
                },
            ]
        })
        findings = _parse_output(raw, {"intent_id": "i-1"})
        assert len(findings) == 2
        assert findings[0].category == FindingCategory.SAST
        assert findings[0].severity == FindingSeverity.MEDIUM
        assert findings[0].file == "app.py"
        assert findings[1].severity == FindingSeverity.HIGH

    def test_empty_output(self, db_path):
        from converge.adapters.security.bandit_adapter import _parse_output
        assert _parse_output("", {}) == []

    def test_invalid_json(self, db_path):
        from converge.adapters.security.bandit_adapter import _parse_output
        assert _parse_output("not json", {}) == []


class TestPipAuditAdapter:
    """pip-audit SCA scanner adapter."""

    def test_scanner_name(self, db_path):
        assert PipAuditScanner().scanner_name == "pip-audit"

    def test_parse_output(self, db_path):
        from converge.adapters.security.pip_audit_adapter import _parse_output
        raw = json.dumps([
            {
                "name": "requests",
                "version": "2.25.0",
                "vulns": [
                    {
                        "id": "CVE-2021-1234",
                        "description": "SSRF vulnerability",
                        "fix_versions": ["2.26.0"],
                        "cvss": 7.5,
                    }
                ],
            }
        ])
        findings = _parse_output(raw, {})
        assert len(findings) == 1
        assert findings[0].category == FindingCategory.SCA
        assert findings[0].severity == FindingSeverity.HIGH
        assert "requests" in findings[0].file

    def test_no_vulns(self, db_path):
        from converge.adapters.security.pip_audit_adapter import _parse_output
        raw = json.dumps([{"name": "requests", "version": "2.31.0", "vulns": []}])
        assert _parse_output(raw, {}) == []


class TestGitleaksAdapter:
    """Gitleaks secrets scanner adapter."""

    def test_scanner_name(self, db_path):
        assert GitleaksScanner().scanner_name == "gitleaks"

    def test_parse_output(self, db_path):
        from converge.adapters.security.gitleaks_adapter import _parse_output
        raw = json.dumps([
            {
                "RuleID": "aws-access-key",
                "File": "config.py",
                "StartLine": 5,
                "Match": "AKIAIOSFODNN7EXAMPLE",
            }
        ])
        findings = _parse_output(raw, {})
        assert len(findings) == 1
        assert findings[0].category == FindingCategory.SECRETS
        assert findings[0].severity == FindingSeverity.HIGH
        assert findings[0].rule == "aws-access-key"
        # Evidence should be partially redacted
        assert "AKIAIOSF" in findings[0].evidence
        assert "*" in findings[0].evidence

    def test_empty_results(self, db_path):
        from converge.adapters.security.gitleaks_adapter import _parse_output
        assert _parse_output("", {}) == []
        assert _parse_output("[]", {}) == []


class TestShellAdapter:
    """Shell command scanner adapter."""

    def test_scanner_name(self, db_path):
        assert ShellScanner().scanner_name == "shell"

    def test_always_available(self, db_path):
        assert ShellScanner().is_available() is True

    def test_parse_json_output(self, db_path):
        from converge.adapters.security.shell_adapter import _try_parse_json
        raw = json.dumps({
            "findings": [
                {"severity": "high", "category": "sast", "file": "x.py",
                 "rule": "custom-1", "evidence": "bad pattern"},
            ]
        })
        findings = _try_parse_json(raw, {})
        assert findings is not None
        assert len(findings) == 1
        assert findings[0].severity == FindingSeverity.HIGH

    def test_non_json_returns_none(self, db_path):
        from converge.adapters.security.shell_adapter import _try_parse_json
        assert _try_parse_json("plain text", {}) is None


# ---------------------------------------------------------------------------
# AR-39: Security gate in policy evaluation
# ---------------------------------------------------------------------------

class TestSecurityGate:
    """Security gate integration in policy evaluation."""

    def test_no_findings_passes(self, db_path):
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=5.0,
            containment_score=0.8,
            security_findings=[],
        )
        sec_gates = [g for g in result.gates if g.gate == GateName.SECURITY]
        assert len(sec_gates) == 1
        assert sec_gates[0].passed is True

    def test_critical_findings_blocks(self, db_path):
        findings = [{"severity": "critical", "scanner": "bandit"}]
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=5.0,
            containment_score=0.8,
            security_findings=findings,
        )
        sec_gates = [g for g in result.gates if g.gate == GateName.SECURITY]
        assert len(sec_gates) == 1
        assert sec_gates[0].passed is False
        assert result.verdict.value == "BLOCK"

    def test_high_findings_within_threshold(self, db_path):
        """Medium risk allows up to 2 high findings."""
        findings = [
            {"severity": "high", "scanner": "bandit"},
            {"severity": "high", "scanner": "gitleaks"},
        ]
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=5.0,
            containment_score=0.8,
            security_findings=findings,
        )
        sec_gates = [g for g in result.gates if g.gate == GateName.SECURITY]
        assert sec_gates[0].passed is True

    def test_high_findings_exceed_threshold(self, db_path):
        """3 high findings exceeds medium risk threshold of 2."""
        findings = [
            {"severity": "high"}, {"severity": "high"}, {"severity": "high"},
        ]
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=5.0,
            containment_score=0.8,
            security_findings=findings,
        )
        sec_gates = [g for g in result.gates if g.gate == GateName.SECURITY]
        assert sec_gates[0].passed is False

    def test_no_security_findings_param_skips_gate(self, db_path):
        """When security_findings is None, the gate is not evaluated."""
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=5.0,
            containment_score=0.8,
        )
        sec_gates = [g for g in result.gates if g.gate == GateName.SECURITY]
        assert len(sec_gates) == 0

    def test_medium_low_findings_pass(self, db_path):
        """Medium and low severity findings don't block."""
        findings = [
            {"severity": "medium"}, {"severity": "low"}, {"severity": "info"},
        ]
        result = evaluate(
            risk_level=RiskLevel.MEDIUM,
            checks_passed=["lint"],
            entropy_delta=5.0,
            containment_score=0.8,
            security_findings=findings,
        )
        sec_gates = [g for g in result.gates if g.gate == GateName.SECURITY]
        assert sec_gates[0].passed is True

    def test_backward_compatible(self, db_path):
        """Existing tests that don't pass security_findings still work."""
        result = evaluate(
            risk_level=RiskLevel.LOW,
            checks_passed=["lint"],
            entropy_delta=5.0,
            containment_score=0.5,
        )
        assert result.verdict.value == "ALLOW"
        assert len(result.gates) == 3  # verification, containment, entropy


# ---------------------------------------------------------------------------
# Security orchestrator (run_scan)
# ---------------------------------------------------------------------------

class TestSecurityOrchestrator:
    """Security scanning orchestrator."""

    def test_run_scan_with_mock_scanner(self, db_path):
        mock_scanner = MagicMock()
        mock_scanner.scanner_name = "mock"
        mock_scanner.is_available.return_value = True
        mock_scanner.scan.return_value = [
            SecurityFinding(
                id="mf-1", scanner="mock", category=FindingCategory.SAST,
                severity=FindingSeverity.HIGH, file="x.py", line=1,
                rule="test-rule", evidence="test",
            ),
        ]

        result = security.run_scan(
        "/tmp/test", scanners=[mock_scanner], intent_id="i-1",
        )
        assert result["total_findings"] == 1
        assert result["severity_counts"]["high"] == 1
        assert len(result["scanners"]) == 1
        assert result["scanners"][0]["status"] == "completed"

    def test_unavailable_scanner_skipped(self, db_path):
        mock_scanner = MagicMock()
        mock_scanner.scanner_name = "unavailable"
        mock_scanner.is_available.return_value = False

        result = security.run_scan("/tmp", scanners=[mock_scanner])
        assert result["total_findings"] == 0
        assert result["scanners"][0]["status"] == "skipped"

    def test_scan_emits_events(self, db_path):
        mock_scanner = MagicMock()
        mock_scanner.scanner_name = "mock"
        mock_scanner.is_available.return_value = True
        mock_scanner.scan.return_value = []

        security.run_scan("/tmp", scanners=[mock_scanner])
        started = event_log.query(event_type=EventType.SECURITY_SCAN_STARTED)
        completed = event_log.query(event_type=EventType.SECURITY_SCAN_COMPLETED)
        assert len(started) == 1
        assert len(completed) == 1

    def test_critical_finding_emits_detection_event(self, db_path):
        mock_scanner = MagicMock()
        mock_scanner.scanner_name = "mock"
        mock_scanner.is_available.return_value = True
        mock_scanner.scan.return_value = [
            SecurityFinding(
                id="cf-1", scanner="mock", category=FindingCategory.SAST,
                severity=FindingSeverity.CRITICAL, file="x.py",
                rule="B1", evidence="critical issue",
            ),
        ]

        security.run_scan("/tmp", scanners=[mock_scanner])
        detected = event_log.query(event_type=EventType.SECURITY_FINDING_DETECTED)
        assert len(detected) == 1

    def test_findings_persisted_to_store(self, db_path):
        mock_scanner = MagicMock()
        mock_scanner.scanner_name = "mock"
        mock_scanner.is_available.return_value = True
        mock_scanner.scan.return_value = [
            SecurityFinding(
                id="pf-1", scanner="mock", category=FindingCategory.SAST,
                severity=FindingSeverity.MEDIUM, file="y.py",
            ),
        ]

        security.run_scan("/tmp", scanners=[mock_scanner], intent_id="i-1")
        stored = event_log.list_security_findings(intent_id="i-1")
        assert len(stored) == 1

    def test_scan_summary(self, db_path):
        # Add some findings
        for i, sev in enumerate(["critical", "high", "medium"]):
            event_log.upsert_security_finding({
                "id": f"s-{i}", "scanner": "test",
                "category": "sast", "severity": sev,
            })
        summary = security.scan_summary()
        assert summary["finding_counts"]["total"] == 3
        assert summary["finding_counts"]["critical"] == 1

    def test_multiple_scanners(self, db_path):
        scanner1 = MagicMock()
        scanner1.scanner_name = "s1"
        scanner1.is_available.return_value = True
        scanner1.scan.return_value = [
            SecurityFinding(
                id="s1-1", scanner="s1", category=FindingCategory.SAST,
                severity=FindingSeverity.LOW,
            ),
        ]
        scanner2 = MagicMock()
        scanner2.scanner_name = "s2"
        scanner2.is_available.return_value = True
        scanner2.scan.return_value = [
            SecurityFinding(
                id="s2-1", scanner="s2", category=FindingCategory.SECRETS,
                severity=FindingSeverity.HIGH,
            ),
        ]

        result = security.run_scan("/tmp", scanners=[scanner1, scanner2])
        assert result["total_findings"] == 2
        assert len(result["scanners"]) == 2


# ---------------------------------------------------------------------------
# AR-40: CLI wiring (import check)
# ---------------------------------------------------------------------------

class TestSecurityCLIWiring:
    """CLI commands are wired up correctly."""

    def test_dispatch_keys_exist(self, db_path):
        from converge.cli import _DISPATCH
        assert ("security", "scan") in _DISPATCH
        assert ("security", "findings") in _DISPATCH
        assert ("security", "summary") in _DISPATCH

    def test_subcmd_attr(self, db_path):
        from converge.cli import _SUBCMD_ATTR
        assert _SUBCMD_ATTR["security"] == "security_cmd"
