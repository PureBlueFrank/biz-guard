"""Canonical input/output schemas for the single change evaluator."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from bizguard.decision.v2 import DecisionState, FindingV2
from bizguard.semantic.models import CatalogRequiredTest


class EvaluationRequest(BaseModel):
    """The only input shape accepted by the canonical change evaluator."""

    diff_text: str
    repository_root: Path
    base_revisions: dict[str, object] = Field(default_factory=dict)
    policy_revision: str = "phase5"
    principal: str = "engineering"
    tests_passed: bool | None = True
    change_context_id: str | None = None
    trace_id: str | None = None


class ChangeDecision(BaseModel):
    """The only output shape produced by the canonical change evaluator."""

    decision: DecisionState
    rationale: str
    findings: list[FindingV2] = Field(default_factory=list)
    required_tests: list[CatalogRequiredTest] = Field(default_factory=list)
    required_approvers: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    risk_score: float
    change_context_id: str | None = None
    policy_revision: str
    base_revisions_sha256: str
    approval_state: str | None = None
    trace_id: str | None = None
