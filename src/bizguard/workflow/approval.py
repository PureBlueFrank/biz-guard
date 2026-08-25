"""Idempotent approval, co-sign, delegation, evidence and scoped waiver handling."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from copy import deepcopy
from threading import RLock
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from pydantic import BaseModel, Field, model_validator

from bizguard.observability import AuditEvent, AuditTrail
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

    def __init__(self, store: ApprovalStore | None = None) -> None:
        self._store = store
        self._cache: dict[tuple[str, str, str, tuple[str, ...]], ApprovalRequest] = {}
        self.audit = AuditTrail()

    @_synchronized
    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        if self._store is not None:
            committed_events: list[AuditEvent] = []

            def operation(persisted: str | None) -> tuple[str, str, list[str]]:
                if persisted is not None:
                    restored = ApprovalRequest.model_validate_json(persisted)
                    updated_at = restored.updated_at or restored.created_at or datetime.now(timezone.utc)
                    return persisted, updated_at.isoformat(), []
                current = request.model_copy(deep=True)
                now = datetime.now(timezone.utc)
                current.created_at = current.created_at or now
                current.updated_at = now
                committed_events.append(
                    self._event(
                        "approval_created",
                        current,
                        policy_revision=current.policy_revision,
                        requested_by=current.requested_by,
                    )
                )
                return (
                    current.model_dump_json(),
                    now.isoformat(),
                    [event.json_line() for event in committed_events],
                )

            payload = self._store.mutate(
                request.change_context_id,
                request.policy_revision,
                self._approver_set(request),
                operation,
            )
            restored = ApprovalRequest.model_validate_json(payload)
            self.audit.events.extend(committed_events)
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
        self.audit.events.append(
            self._event(
                "approval_created",
                request,
                policy_revision=request.policy_revision,
                requested_by=request.requested_by,
            )
        )
        return request

    @_synchronized
    def delegate(self, request: ApprovalRequest, approver: str, delegate: str) -> None:
        def operation(current: ApprovalRequest) -> list[AuditEvent]:
            if approver not in current.approvers:
                raise ValueError("only configured approvers may delegate")
            current.delegates[approver] = delegate
            return [self._event("approval_delegated", current, approver=approver, delegate=delegate)]

        self._mutate(request, operation)

    @_synchronized
    def add_evidence(self, request: ApprovalRequest, evidence: str) -> None:
        def operation(current: ApprovalRequest) -> list[AuditEvent]:
            current.state = transition(current.state, ApprovalState.PENDING)
            current.evidence_refs.append(evidence)
            return [self._event("evidence_added", current, evidence=evidence)]

        self._mutate(request, operation)

    @_synchronized
    def request_evidence(self, request: ApprovalRequest, reason: str) -> None:
        def operation(current: ApprovalRequest) -> list[AuditEvent]:
            current.state = transition(current.state, ApprovalState.EVIDENCE_REQUESTED)
            return [self._event("evidence_requested", current, reason=reason)]

        self._mutate(request, operation)

    @_synchronized
    def approve(self, request: ApprovalRequest, actor: str) -> None:
        def operation(current: ApprovalRequest) -> list[AuditEvent]:
            eligible = set(current.approvers) | set(current.delegates.values())
            if actor not in eligible:
                raise ValueError("actor is not an approver or delegate")
            approval_slot = next(
                (
                    approver
                    for approver, delegate in current.delegates.items()
                    if delegate == actor
                ),
                actor,
            )
            current.approvals.add(approval_slot)
            events = [self._event("approval_recorded", current, actor=actor)]
            if len(current.approvals) >= current.required_cosigns:
                current.state = transition(current.state, ApprovalState.APPROVED)
                events.append(self._event("approval_granted", current))
            return events

        self._mutate(request, operation)

    @_synchronized
    def reject(self, request: ApprovalRequest, actor: str, reason: str) -> None:
        def operation(current: ApprovalRequest) -> list[AuditEvent]:
            if actor not in set(current.approvers) | set(current.delegates.values()):
                raise ValueError("actor is not an approver or delegate")
            current.state = transition(current.state, ApprovalState.REJECTED)
            return [self._event("approval_rejected", current, actor=actor, reason=reason)]

        self._mutate(request, operation)

    @_synchronized
    def grant_waiver(self, request: ApprovalRequest, waiver: Waiver) -> None:
        if not waiver.scope or not waiver.reason or not waiver.compensating_control:
            raise ValueError("waiver requires scope, reason, and compensating control")

        def operation(current: ApprovalRequest) -> list[AuditEvent]:
            current.waiver = waiver
            return [self._event("waiver_granted", current, scope=waiver.scope)]

        self._mutate(request, operation)

    @_synchronized
    def expire_or_escalate(self, request: ApprovalRequest, deadline: datetime, now: datetime | None = None) -> None:
        def operation(current: ApprovalRequest) -> list[AuditEvent]:
            if (now or datetime.now(timezone.utc)) < deadline or current.state not in {
                ApprovalState.PENDING,
                ApprovalState.EVIDENCE_REQUESTED,
            }:
                return []
            current.state = transition(current.state, ApprovalState.ESCALATED)
            return [self._event("approval_escalated", current)]

        self._mutate(request, operation)

    def _approver_set(self, request: ApprovalRequest) -> str:
        return f"{','.join(sorted(request.approvers))}@{request.decision_fingerprint}"

    def _mutate(
        self,
        request: ApprovalRequest,
        operation: Callable[[ApprovalRequest], list[AuditEvent]],
    ) -> None:
        committed_events: list[AuditEvent] = []
        if self._store is None:
            committed_events.extend(operation(request))
            request.updated_at = datetime.now(timezone.utc)
            self.audit.events.extend(committed_events)
            self._cache[request.key] = request
            return

        def persisted_operation(payload: str | None) -> tuple[str, str, list[str]]:
            if payload is None:
                raise ValueError("approval request unavailable")
            current = ApprovalRequest.model_validate_json(payload)
            committed_events.extend(operation(current))
            current.updated_at = datetime.now(timezone.utc)
            return (
                current.model_dump_json(),
                current.updated_at.isoformat(),
                [event.json_line() for event in committed_events],
            )

        payload = self._store.mutate(
            request.change_context_id,
            request.policy_revision,
            self._approver_set(request),
            persisted_operation,
        )
        restored = ApprovalRequest.model_validate_json(payload)
        for field_name in ApprovalRequest.model_fields:
            setattr(request, field_name, deepcopy(getattr(restored, field_name)))
        self.audit.events.extend(committed_events)
        self._cache[request.key] = request

    def _event(self, action: str, request: ApprovalRequest, **details: str) -> AuditEvent:
        temporary = AuditTrail()
        return temporary.add(action, request.change_context_id, **details)
