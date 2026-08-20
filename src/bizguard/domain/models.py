"""Strict, machine-readable contracts shared by future BizGuard phases."""

from pydantic import BaseModel, ConfigDict, Field

from bizguard.domain.enums import (
    ArtifactStatus,
    CheckStatus,
    DecisionState,
    EvidenceLevel,
    PolicyMode,
    UnknownReason,
)


class ContractModel(BaseModel):
    """Base class that rejects accidental, unversioned contract fields."""

    model_config = ConfigDict(extra="forbid")


class Evidence(ContractModel):
    """A revision-pinned, inspectable input to a conclusion."""

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    revision: str = Field(min_length=1)
    evidence_uri: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9+.-]*://")
    level: EvidenceLevel = EvidenceLevel.FACT


class ChangedArtifact(ContractModel):
    """A versioned source, API, schema, database, or message artifact."""

    id: str = Field(min_length=1)
    uri: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9+.-]*://")
    status: ArtifactStatus
    revision: str = Field(min_length=1)
    evidence: list[Evidence] = Field(default_factory=list)


class ChangeContext(ContractModel):
    """The immutable input boundary for one evaluated change."""

    id: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    diff_uri: str = Field(min_length=1, pattern=r"^repo://")
    caller: str | None = Field(default=None, min_length=1)
    principal: str | None = Field(default=None, min_length=1)
    artifacts: list[ChangedArtifact] = Field(default_factory=list)
    visible_knowledge_ids: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class ImpactPath(ContractModel):
    """An ordered cross-boundary path backed by versioned evidence."""

    id: str = Field(min_length=1)
    node_ids: list[str] = Field(min_length=2)
    evidence: list[Evidence] = Field(min_length=1)
    unknown_reason: UnknownReason | None = None


class RequiredTest(ContractModel):
    """A test that must be executed or explicitly carried as a condition."""

    id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    command: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class Finding(ContractModel):
    """One policy or analysis conclusion."""

    id: str = Field(min_length=1)
    status: CheckStatus
    message: str = Field(min_length=1)
    policy_mode: PolicyMode | None = None
    severity: str | None = Field(default=None, min_length=1)
    remediation: str | None = Field(default=None, min_length=1)
    evidence: list[Evidence] = Field(min_length=1)
    unknown_reason: UnknownReason | None = None


class Decision(ContractModel):
    """The version-two decision, its supporting facts, and follow-up tests."""

    outcome: DecisionState
    findings: list[Finding] = Field(default_factory=list)
    required_tests: list[RequiredTest] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class ApprovalRequest(ContractModel):
    """The explicitly reviewable record required for an uncertain boundary."""

    id: str = Field(min_length=1)
    decision: Decision
    requested_by: str = Field(min_length=1)
    reason: UnknownReason
    evidence: list[Evidence] = Field(min_length=1)
