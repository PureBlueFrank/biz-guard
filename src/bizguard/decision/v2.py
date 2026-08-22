"""Ordered, explainable four-state change decision."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from bizguard.decision.legacy import Decision, FindingStatus, evaluate_change
from bizguard.risk.engine import score


class DecisionState(StrEnum):
    """Enumerate the possible aggregate change decisions."""

    ALLOW = "ALLOW"
    ALLOW_WITH_TESTS = "ALLOW_WITH_TESTS"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"


class FindingV2(BaseModel):
    """A complete finding: callers never infer risk from an opaque string."""

    id: str
    severity: str = "medium"
    effect: str
    remediation: str
    required_approver: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    violated: bool = False
    critical_unknown: bool = False
    public_contract: bool = False
    owner: str | None = None


class DecisionInput(BaseModel):
    """Define evidence and thresholds consumed by the decision engine."""

    findings: list[FindingV2] = Field(default_factory=list)
    tests_passed: bool | None = None
    required_tests: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    version_known: bool = True
    risk_score: float | None = Field(default=None, ge=0.0)
    approval_threshold: float = Field(default=0.7, ge=0.0)
    approval_satisfied: bool = False


class DecisionResult(BaseModel):
    """Describe the aggregate decision and its required follow-up."""

    decision: DecisionState
    rationale: str
    required_tests: list[str] = Field(default_factory=list)
    required_approvers: list[str] = Field(default_factory=list)
    risk_score: float
    evidence: list[str] = Field(default_factory=list)


def decide(data: DecisionInput) -> DecisionResult:
    """Apply the non-negotiable hard-condition order before risk scoring."""
    risk_score = score(data.findings) if data.risk_score is None else data.risk_score
    critical = [item for item in data.findings if item.violated and item.severity == "critical"]
    if critical:
        return _result(DecisionState.BLOCK, "critical policy violation", data, critical)
    unknown = [item for item in data.findings if item.critical_unknown]
    if (unknown or not data.version_known) and not data.approval_satisfied:
        return _result(DecisionState.REQUIRE_APPROVAL, "critical boundary or version is unknown", data, unknown)
    if data.required_tests and data.tests_passed is not True:
        return _result(DecisionState.ALLOW_WITH_TESTS, "required test evidence is missing", data, [])
    public = [item for item in data.findings if item.public_contract]
    if (public or len(set(data.owners)) > 1) and not data.approval_satisfied:
        return _result(DecisionState.REQUIRE_APPROVAL, "public contract or multiple owners", data, public)
    if risk_score >= data.approval_threshold and not data.approval_satisfied:
        return _result(DecisionState.REQUIRE_APPROVAL, "risk score requires approval", data, [])
    return _result(DecisionState.ALLOW, "all hard conditions and test evidence satisfied", data, [])


def decide_diff(diff_text: str) -> DecisionResult:
    """Adapt the established deterministic diff pipeline to the v2 public contract."""
    card = evaluate_change(diff_text)
    findings = [
        FindingV2(
            id=item.finding_id,
            severity="critical" if item.status is FindingStatus.VIOLATED else "medium",
            effect=item.message,
            remediation="resolve the reported policy finding",
            confidence=1.0,
            violated=item.status is FindingStatus.VIOLATED,
            critical_unknown=item.status is FindingStatus.INCOMPLETE,
        )
        for item in card.findings
    ]
    if card.decision is Decision.ALLOW:
        return decide(DecisionInput(findings=findings, tests_passed=True))
    if card.decision is Decision.BLOCK:
        return decide(DecisionInput(findings=findings, tests_passed=True))
    return decide(DecisionInput(findings=findings, tests_passed=True, version_known=False))


def _approvers(data: DecisionInput) -> list[str]:
    return sorted({*data.owners, *(item.required_approver for item in data.findings if item.required_approver)})


def _result(state: DecisionState, rationale: str, data: DecisionInput, related: list[FindingV2]) -> DecisionResult:
    return DecisionResult(
        decision=state,
        rationale=rationale,
        required_tests=sorted(set(data.required_tests)) if state is DecisionState.ALLOW_WITH_TESTS else [],
        required_approvers=_approvers(data) if state is DecisionState.REQUIRE_APPROVAL else [],
        risk_score=score(data.findings) if data.risk_score is None else data.risk_score,
        evidence=[item.id for item in related],
    )
