"""Idempotent approval, co-sign, delegation, evidence and scoped waiver handling."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from bizguard.observability import AuditTrail
from .state_machine import ApprovalState, transition


class Waiver(BaseModel):
    """Describe a time-bounded waiver and its compensating control."""

    scope: str
    reason: str
    compensating_control: str
    expires_at: datetime

    def active(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) < self.expires_at


class ApprovalRequest(BaseModel):
    """Track approval state, approvers, delegations, and waivers."""

    change_context_id: str
    policy_revision: str
    approvers: tuple[str, ...]
    required_cosigns: int = Field(ge=1)
    state: ApprovalState = ApprovalState.PENDING
    approvals: set[str] = Field(default_factory=set)
    delegates: dict[str, str] = Field(default_factory=dict)
    waiver: Waiver | None = None

    @property
    def key(self) -> tuple[str, str, tuple[str, ...]]:
        return (self.change_context_id, self.policy_revision, tuple(sorted(self.approvers)))


class ApprovalService:
    """Manage approval requests and append their audit events."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self._requests: dict[tuple[str, str, tuple[str, ...]], ApprovalRequest] = {}
        self.audit = AuditTrail()

    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        if not self.available:
            self.audit.add("approval_unavailable", request.change_context_id)
            return request
        existing = self._requests.get(request.key)
        if existing is not None:
            return existing
        self._requests[request.key] = request
        self.audit.add("approval_created", request.change_context_id, policy_revision=request.policy_revision)
        return request

    def delegate(self, request: ApprovalRequest, approver: str, delegate: str) -> None:
        if approver not in request.approvers:
            raise ValueError("only configured approvers may delegate")
        request.delegates[approver] = delegate
        self.audit.add("approval_delegated", request.change_context_id, approver=approver, delegate=delegate)

    def add_evidence(self, request: ApprovalRequest, evidence: str) -> None:
        request.state = transition(request.state, ApprovalState.PENDING)
        self.audit.add("evidence_added", request.change_context_id, evidence=evidence)

    def request_evidence(self, request: ApprovalRequest, reason: str) -> None:
        request.state = transition(request.state, ApprovalState.EVIDENCE_REQUESTED)
        self.audit.add("evidence_requested", request.change_context_id, reason=reason)

    def approve(self, request: ApprovalRequest, actor: str) -> None:
        eligible = set(request.approvers) | set(request.delegates.values())
        if actor not in eligible:
            raise ValueError("actor is not an approver or delegate")
        request.approvals.add(actor)
        self.audit.add("approval_recorded", request.change_context_id, actor=actor)
        if len(request.approvals) >= request.required_cosigns:
            request.state = transition(request.state, ApprovalState.APPROVED)
            self.audit.add("approval_granted", request.change_context_id)

    def reject(self, request: ApprovalRequest, actor: str, reason: str) -> None:
        if actor not in set(request.approvers) | set(request.delegates.values()):
            raise ValueError("actor is not an approver or delegate")
        request.state = transition(request.state, ApprovalState.REJECTED)
        self.audit.add("approval_rejected", request.change_context_id, actor=actor, reason=reason)

    def grant_waiver(self, request: ApprovalRequest, waiver: Waiver) -> None:
        if not waiver.scope or not waiver.reason or not waiver.compensating_control:
            raise ValueError("waiver requires scope, reason, and compensating control")
        request.waiver = waiver
        self.audit.add("waiver_granted", request.change_context_id, scope=waiver.scope)

    def expire_or_escalate(self, request: ApprovalRequest, deadline: datetime, now: datetime | None = None) -> None:
        if (now or datetime.now(timezone.utc)) >= deadline and request.state in {ApprovalState.PENDING, ApprovalState.EVIDENCE_REQUESTED}:
            request.state = transition(request.state, ApprovalState.ESCALATED)
            self.audit.add("approval_escalated", request.change_context_id)
