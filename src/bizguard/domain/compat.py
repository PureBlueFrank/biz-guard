"""Read-only translation from the MVP card to the version-two decision state."""

from bizguard.decision import FaultCode
from bizguard.domain.enums import DecisionState, UnknownReason


_APPROVAL_FAULTS = frozenset(
    {
        UnknownReason.INDEX_LAG,
        UnknownReason.REVISION_MISMATCH,
        UnknownReason.PERMISSION_DENIED,
        UnknownReason.DYNAMIC_BOUNDARY,
    }
)
_TEST_FAULTS = frozenset({UnknownReason.TEST_EVIDENCE_MISSING})
_BLOCKING_FAULTS = frozenset({UnknownReason.MALFORMED_INPUT, UnknownReason.UNSUPPORTED_DIFF})

_LEGACY_PRIORITIES = {
    FaultCode.INPUT_VALIDATION: 0,
    FaultCode.DIFF_PARSE: 0,
    FaultCode.CACHE_CORRUPT: 0,
    FaultCode.POLICY_UNCOVERED: 1,
    FaultCode.RETRIEVAL_EMPTY: 1,
    FaultCode.EMBEDDING_TIMEOUT: 1,
    FaultCode.MCP_DISCONNECTED: 1,
}


def map_check_incomplete(fault_code: object | None) -> DecisionState:
    """Map an incomplete MVP check conservatively; this function never returns ALLOW."""
    raw_code = getattr(fault_code, "value", fault_code)
    try:
        code = UnknownReason(str(raw_code))
    except ValueError:
        return DecisionState.BLOCK
    if code in _APPROVAL_FAULTS:
        return DecisionState.REQUIRE_APPROVAL
    if code in _TEST_FAULTS:
        return DecisionState.ALLOW_WITH_TESTS
    if code in _BLOCKING_FAULTS:
        return DecisionState.BLOCK
    return DecisionState.BLOCK


def map_legacy_decision(card: object) -> DecisionState:
    """Translate an MVP card without modifying its legacy three-state output.

    A legacy successful card remains successful.  Every CHECK_INCOMPLETE card is
    routed through its highest-priority fault and can never become ALLOW.
    """
    decision = getattr(card, "decision", None)
    legacy = getattr(decision, "value", decision)
    if legacy == "ALLOW":
        return DecisionState.ALLOW
    if legacy == "BLOCK":
        return DecisionState.BLOCK
    faults = getattr(card, "faults", [])
    if not faults:
        return DecisionState.BLOCK
    ordered_faults = sorted(faults, key=_legacy_fault_priority)
    return _map_legacy_fault(getattr(ordered_faults[0], "code", None))


def _legacy_fault_priority(fault: object) -> int:
    """Order legacy faults so a weak degradation cannot mask malformed input."""
    raw_code = getattr(getattr(fault, "code", None), "value", getattr(fault, "code", None))
    try:
        return _LEGACY_PRIORITIES[FaultCode(str(raw_code))]
    except ValueError:
        return 0


def _map_legacy_fault(fault_code: object | None) -> DecisionState:
    """Map the MVP FaultCode vocabulary to the v2 conservative outcomes."""
    raw_code = getattr(fault_code, "value", fault_code)
    try:
        code = FaultCode(str(raw_code))
    except ValueError:
        return DecisionState.BLOCK
    if code in {
        FaultCode.POLICY_UNCOVERED,
        FaultCode.RETRIEVAL_EMPTY,
        FaultCode.EMBEDDING_TIMEOUT,
        FaultCode.MCP_DISCONNECTED,
    }:
        return DecisionState.REQUIRE_APPROVAL
    return DecisionState.BLOCK
