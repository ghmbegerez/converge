"""Shared constants for risk scoring."""

_RISK_BONUS = {"low": 0, "medium": 5, "high": 15, "critical": 30}
_CORE_TARGETS = {"main", "master", "release", "production", "prod"}
_CORE_PATHS = {"src/", "lib/", "core/", "pkg/", "internal/", "app/"}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
