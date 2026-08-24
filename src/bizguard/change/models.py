"""Canonical input/output schemas for the single change evaluator."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from bizguard.decision.v2 import DecisionState, FindingV2, NextAction
from bizguard.semantic.models import CatalogRequiredTest


class TestEvidence(BaseModel):
    """Record one revision-bound test result supplied by a trusted runner."""

    test_id: str
    passed: bool
    revision: str
    evidence_uri: str


class EvaluationRequest(BaseModel):
    """The only input shape accepted by the canonical change evaluator."""

    diff_text: str
    repository_root: Path
    base_revisions: dict[str, object] = Field(default_factory=dict)
    policy_revision: str = "phase5"
    principal: str = "engineering"
    tests_passed: bool | None = None
    test_evidence: list[TestEvidence] = Field(default_factory=list)
    change_context_id: str | None = None
    trace_id: str | None = None
    prepared_required_tests: list[str] | None = None
    prepared_required_approvers: list[str] | None = None
    prepared_graph_content_digest: str | None = None
    prepared_knowledge_content_digest: str | None = None


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
    decision_fingerprint: str
    approval_state: str | None = None
    trace_id: str | None = None
    shadow_findings: list[str] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
