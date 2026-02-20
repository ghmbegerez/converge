"""Pydantic request/response models for strict input validation."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

class RiskPolicyBody(BaseModel):
    tenant_id: str | None = None
    max_risk_score: float | None = None
    max_blast_severity: str | None = None
    entropy_budget: float | None = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class AgentPolicyBody(BaseModel):
    agent_id: str = Field(..., min_length=1, description="Agent identifier")
    tenant_id: str | None = None
    atl: int = Field(default=0, ge=0, le=3)
    max_risk_score: float = 30.0
    max_blast_severity: str = "low"
    min_test_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    require_compliance_pass: bool = True
    require_human_approval: bool = True
    require_dual_approval_on_critical: bool = True
    allow_actions: list[str] = Field(default_factory=lambda: ["analyze"])
    action_overrides: dict = Field(default_factory=dict)
    expires_at: str | None = None

    model_config = {"extra": "allow"}


class AgentAuthorizeBody(BaseModel):
    agent_id: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    intent_id: str = Field(..., min_length=1)
    tenant_id: str | None = None
    human_approvals: int = 0


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------

class ComplianceThresholdsBody(BaseModel):
    tenant_id: str | None = None
    mergeable_rate: float | None = None
    conflict_rate: float | None = None
    max_retries: int | None = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Auth / Key rotation
# ---------------------------------------------------------------------------

class KeyRotateBody(BaseModel):
    grace_period_seconds: int = Field(default=3600, ge=60, le=86400)
