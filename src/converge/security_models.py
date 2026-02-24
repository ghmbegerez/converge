"""Security finding data types.

Extracted from models.py to keep the domain model module under the LOC limit.
Re-exported from models.py for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(str, Enum):
    SAST = "sast"
    SCA = "sca"
    SECRETS = "secrets"


@dataclass
class SecurityFinding:
    id: str
    scanner: str                          # bandit, pip-audit, gitleaks
    category: FindingCategory             # sast, sca, secrets
    severity: FindingSeverity             # critical..info
    file: str = ""
    line: int = 0
    rule: str = ""
    evidence: str = ""
    confidence: str = "medium"            # high, medium, low
    intent_id: str | None = None
    tenant_id: str | None = None
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scanner": self.scanner,
            "category": self.category.value,
            "severity": self.severity.value,
            "file": self.file,
            "line": self.line,
            "rule": self.rule,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "intent_id": self.intent_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
        }
