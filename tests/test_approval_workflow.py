from datetime import datetime, timedelta, timezone

import pytest

from bizguard.workflow.approval import ApprovalRequest, ApprovalService, Waiver
from bizguard.workflow.state_machine import ApprovalState


def test_approval_idempotence_and_cosign() -> None:
    service = ApprovalService()
    request = service.create(ApprovalRequest(change_context_id="c", policy_revision="r", approvers=("a", "b"), required_cosigns=2))
    assert service.create(request) is request
    service.approve(request, "a")
    service.approve(request, "b")
    assert request.state is ApprovalState.APPROVED


def test_rejection_and_evidence_follow_real_transitions() -> None:
    service = ApprovalService()
    request = ApprovalRequest(change_context_id="c", policy_revision="r", approvers=("a",), required_cosigns=1)
    service.create(request)
    service.request_evidence(request, "need test")
    service.add_evidence(request, "test://run")
    service.reject(request, "a", "unsafe")
    assert request.state is ApprovalState.REJECTED


def test_expired_waiver_is_not_active() -> None:
    waiver = Waiver(scope="change:c", reason="incident", compensating_control="monitor", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert not waiver.active()


def test_unavailable_service_does_not_auto_allow() -> None:
    request = ApprovalService(available=False).create(ApprovalRequest(change_context_id="c", policy_revision="r", approvers=("a",), required_cosigns=1))
    assert request.state is ApprovalState.PENDING


def test_delegate_can_approve() -> None:
    service = ApprovalService()
    request = service.create(
        ApprovalRequest(change_context_id="c", policy_revision="r", approvers=("a",), required_cosigns=1)
    )
    service.delegate(request, "a", "d")
    service.approve(request, "d")
    assert request.state is ApprovalState.APPROVED


def test_bad_waiver_is_rejected() -> None:
    with pytest.raises(ValueError):
        ApprovalService().grant_waiver(ApprovalRequest(change_context_id="c", policy_revision="r", approvers=("a",), required_cosigns=1), Waiver(scope="", reason="", compensating_control="", expires_at=datetime.now(timezone.utc)))


def test_add_evidence_is_a_documented_pending_self_transition() -> None:
    service = ApprovalService()
    request = service.create(ApprovalRequest(change_context_id="c", policy_revision="r", approvers=("a",), required_cosigns=1))
    service.add_evidence(request, "test://run")
    assert request.state is ApprovalState.PENDING
