"""Pydantic request/response models for strict input validation."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

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


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

class ReviewRequestBody(BaseModel):
    intent_id: str = Field(..., min_length=1)
    trigger: str = "policy"
    reviewer: str | None = None
    priority: int | None = None
    tenant_id: str | None = None


class ReviewAssignBody(BaseModel):
    reviewer: str = Field(..., min_length=1)


class ReviewCompleteBody(BaseModel):
    resolution: str = "approved"
    notes: str = ""


class ReviewCancelBody(BaseModel):
    reason: str = ""


class ReviewEscalateBody(BaseModel):
    reason: str = "sla_breach"


# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------

class IntentCreateRequest(BaseModel):
    """Body for POST /intents."""
    source: str = Field(default="", description="Source branch ref")
    target: str = Field(default="main", description="Target branch ref")
    id: str | None = Field(default=None, description="Optional intent ID")
    intent_id: str | None = None
    status: str = "READY"
    risk_level: str = "medium"
    priority: int = 3
    semantic: dict = Field(default_factory=dict)
    technical: dict = Field(default_factory=dict)
    checks_required: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    tenant_id: str | None = None
    plan_id: str | None = None
    origin_type: str = "api"

    model_config = {"extra": "allow"}

    @model_validator(mode="before")
    @classmethod
    def _extract_nested_fields(cls, values: dict) -> dict:
        """Support legacy nested format: technical.source_ref / technical.target_ref."""
        if isinstance(values, dict):
            technical = values.get("technical", {})
            if isinstance(technical, dict):
                if not values.get("source"):
                    values["source"] = technical.get("source_ref", "")
                if not values.get("target"):
                    values["target"] = technical.get("target_ref", "main")
        return values


class IntentEvaluateRequest(BaseModel):
    """Body for POST /intents/evaluate."""
    mode: str = "shadow"

    model_config = {"extra": "allow"}


class IntentValidateRequest(BaseModel):
    """Body for POST /intents/{id}/validate."""
    source: str | None = None
    target: str | None = None
    use_last_simulation: bool = False
    skip_checks: bool = False

    model_config = {"extra": "allow"}
