"""Explicit approval state transitions."""

from enum import StrEnum


class ApprovalState(StrEnum):
    PENDING = "pending"
    EVIDENCE_REQUESTED = "evidence_requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ESCALATED = "escalated"


_TRANSITIONS = {
    ApprovalState.PENDING: {ApprovalState.PENDING, ApprovalState.EVIDENCE_REQUESTED, ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.EXPIRED, ApprovalState.ESCALATED},
    ApprovalState.EVIDENCE_REQUESTED: {ApprovalState.PENDING, ApprovalState.REJECTED, ApprovalState.EXPIRED, ApprovalState.ESCALATED},
    ApprovalState.ESCALATED: {ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.EXPIRED},
}


def transition(current: ApprovalState, target: ApprovalState) -> ApprovalState:
    if target not in _TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal approval transition: {current} -> {target}")
    return target
