"""Idempotent approval, co-sign, delegation, evidence and scoped waiver handling."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from threading import RLock
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from pydantic import BaseModel, Field, model_validator

from bizguard.observability import AuditTrail
from .state_machine import ApprovalState, transition
from .store import ApprovalStore


P = ParamSpec("P")
R = TypeVar("R")
_WORKFLOW_LOCK = RLock()


def _synchronized(method: Callable[P, R]) -> Callable[P, R]:
    """Serialize approval read-modify-write operations inside one service process."""

    @wraps(method)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with _WORKFLOW_LOCK:
            return method(*args, **kwargs)

    return wrapped


class Waiver(BaseModel):
    """Describe a time-bounded waiver and its compensating control."""

    scope: str
    reason: str
    compensating_control: str
    expires_at: datetime

    def active(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) < self.expires_at


class ApprovalRequest(BaseModel):
    """Track approval state, approvers, delegations, evidence and waivers."""

    change_context_id: str
    policy_revision: str
    decision_fingerprint: str = Field(min_length=64, max_length=64)
    approvers: tuple[str, ...]
    required_cosigns: int = Field(ge=1)
    requested_by: str = "engineering"
    state: ApprovalState = ApprovalState.PENDING
    approvals: set[str] = Field(default_factory=set)
    delegates: dict[str, str] = Field(default_factory=dict)
    waiver: Waiver | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def valid_cosign_contract(self) -> "ApprovalRequest":
        """Require distinct approvers and a reachable co-sign threshold."""
        if not self.approvers or len(set(self.approvers)) != len(self.approvers):
            raise ValueError("approval requests require distinct approvers")
        if self.required_cosigns > len(self.approvers):
            raise ValueError("required cosigns cannot exceed the approver count")
        return self

    @property
    def key(self) -> tuple[str, str, str, tuple[str, ...]]:
        return (
            self.change_context_id,
            self.policy_revision,
            self.decision_fingerprint,
            tuple(sorted(self.approvers)),
        )


class ApprovalService:
    """Manage approval requests, persisting them and their audit events."""

    def __init__(self, available: bool = True, store: ApprovalStore | None = None) -> None:
        self.available = available
        self._store = store
        self._cache: dict[tuple[str, str, str, tuple[str, ...]], ApprovalRequest] = {}
        self.audit = AuditTrail()

    @_synchronized
    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        if not self.available:
            self.audit.add("approval_unavailable", request.change_context_id)
            return request
        if self._store is not None:
            persisted = self._store.get(
                request.change_context_id, request.policy_revision, self._approver_set(request)
            )
            if persisted is not None:
                restored = ApprovalRequest.model_validate_json(persisted)
                self._cache[restored.key] = restored
                return restored
        existing = self._cache.get(request.key)
        if existing is not None:
            return existing
        now = datetime.now(timezone.utc)
        if request.created_at is None:
            request.created_at = now
        request.updated_at = now
        self._cache[request.key] = request
        self._persist(request)
        self._record(
            "approval_created",
            request,
            policy_revision=request.policy_revision,
            requested_by=request.requested_by,
        )
        return request

    @_synchronized
    def delegate(self, request: ApprovalRequest, approver: str, delegate: str) -> None:
        if approver not in request.approvers:
            raise ValueError("only configured approvers may delegate")
        request.delegates[approver] = delegate
        self._record("approval_delegated", request, approver=approver, delegate=delegate)
        self._persist(request)

    @_synchronized
    def add_evidence(self, request: ApprovalRequest, evidence: str) -> None:
        request.state = transition(request.state, ApprovalState.PENDING)
        request.evidence_refs.append(evidence)
        self._record("evidence_added", request, evidence=evidence)
        self._persist(request)

    @_synchronized
    def request_evidence(self, request: ApprovalRequest, reason: str) -> None:
        request.state = transition(request.state, ApprovalState.EVIDENCE_REQUESTED)
        self._record("evidence_requested", request, reason=reason)
        self._persist(request)

    @_synchronized
    def approve(self, request: ApprovalRequest, actor: str) -> None:
        eligible = set(request.approvers) | set(request.delegates.values())
        if actor not in eligible:
            raise ValueError("actor is not an approver or delegate")
        approval_slot = next(
            (approver for approver, delegate in request.delegates.items() if delegate == actor),
            actor,
        )
        request.approvals.add(approval_slot)
        self._record("approval_recorded", request, actor=actor)
        if len(request.approvals) >= request.required_cosigns:
            request.state = transition(request.state, ApprovalState.APPROVED)
            self._record("approval_granted", request)
        self._persist(request)

    @_synchronized
    def reject(self, request: ApprovalRequest, actor: str, reason: str) -> None:
        if actor not in set(request.approvers) | set(request.delegates.values()):
            raise ValueError("actor is not an approver or delegate")
        request.state = transition(request.state, ApprovalState.REJECTED)
        self._record("approval_rejected", request, actor=actor, reason=reason)
        self._persist(request)

    @_synchronized
    def grant_waiver(self, request: ApprovalRequest, waiver: Waiver) -> None:
        if not waiver.scope or not waiver.reason or not waiver.compensating_control:
            raise ValueError("waiver requires scope, reason, and compensating control")
        request.waiver = waiver
        self._record("waiver_granted", request, scope=waiver.scope)
        self._persist(request)

    @_synchronized
    def expire_or_escalate(self, request: ApprovalRequest, deadline: datetime, now: datetime | None = None) -> None:
        if (now or datetime.now(timezone.utc)) >= deadline and request.state in {ApprovalState.PENDING, ApprovalState.EVIDENCE_REQUESTED}:
            request.state = transition(request.state, ApprovalState.ESCALATED)
            self._record("approval_escalated", request)
            self._persist(request)

    def _approver_set(self, request: ApprovalRequest) -> str:
        return f"{','.join(sorted(request.approvers))}@{request.decision_fingerprint}"

    def _persist(self, request: ApprovalRequest) -> None:
        request.updated_at = datetime.now(timezone.utc)
        if self._store is not None:
            self._store.put(
                request.change_context_id,
                request.policy_revision,
                self._approver_set(request),
                request.model_dump_json(),
                request.updated_at.isoformat(),
            )

    def _record(self, action: str, request: ApprovalRequest, **details: str) -> None:
        event = self.audit.add(action, request.change_context_id, **details)
        if self._store is not None:
            self._store.append_event(request.change_context_id, event.json_line())
