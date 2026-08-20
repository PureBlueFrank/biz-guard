"""Enumerations owned by the version-two domain contract."""

from enum import StrEnum


class DecisionState(StrEnum):
    """The four outcomes exposed by the version-two protocol."""

    ALLOW = "ALLOW"
    ALLOW_WITH_TESTS = "ALLOW_WITH_TESTS"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"


class EvidenceLevel(StrEnum):
    """How directly an evidence item supports a conclusion."""

    FACT = "FACT"
    INFERENCE = "INFERENCE"
    POLICY = "POLICY"
    UNKNOWN = "UNKNOWN"


class PolicyMode(StrEnum):
    """How a policy participates in a decision."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"


class ArtifactStatus(StrEnum):
    """Version-control state of a changed artifact."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class CheckStatus(StrEnum):
    """Result state of an analysis or policy check."""

    PASSED = "passed"
    VIOLATED = "violated"
    INCOMPLETE = "incomplete"


class UnknownReason(StrEnum):
    """Reasons that prevent a fully automatic conclusion."""

    INDEX_LAG = "INDEX_LAG"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DYNAMIC_BOUNDARY = "DYNAMIC_BOUNDARY"
    TEST_EVIDENCE_MISSING = "TEST_EVIDENCE_MISSING"
    MALFORMED_INPUT = "MALFORMED_INPUT"
    UNSUPPORTED_DIFF = "UNSUPPORTED_DIFF"
    UNKNOWN = "UNKNOWN"


DecisionOutcome = DecisionState
