"""Persistent approval workflow: recovery, idempotency, and gate release."""

from __future__ import annotations

from pathlib import Path

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import EvaluationRequest
from bizguard.ci.check import gate_exit_code
from bizguard.workflow.approval import ApprovalRequest, ApprovalService
from bizguard.workflow.state_machine import ApprovalState
from bizguard.workflow.store import SqliteApprovalStore


def _request(change_context_id: str = "ctx-1") -> ApprovalRequest:
    return ApprovalRequest(
        change_context_id=change_context_id,
        policy_revision="phase5",
        decision_fingerprint="a" * 64,
        approvers=("a", "b"),
        required_cosigns=2,
    )


def test_approval_state_and_audit_survive_store_reopen(tmp_path: Path) -> None:
    db = tmp_path / "approvals.db"
    store = SqliteApprovalStore(db)
    service = ApprovalService(store=store)
    request = service.create(_request())
    service.approve(request, "a")
    service.approve(request, "b")
    store.close()

    reopened = SqliteApprovalStore(db)
    service2 = ApprovalService(store=reopened)
    restored = service2.create(_request())
    assert restored.state is ApprovalState.APPROVED
    assert restored.approvals == {"a", "b"}
    events = reopened.events("ctx-1")
    assert any("approval_granted" in event for event in events)
    assert any("approval_created" in event for event in events)
    reopened.close()


def test_duplicate_request_yields_a_single_approval_record(tmp_path: Path) -> None:
    db = tmp_path / "approvals.db"
    store = SqliteApprovalStore(db)
    service = ApprovalService(store=store)
    request = service.create(_request())
    service.approve(request, "a")
    service.approve(request, "b")
    store.close()

    reopened = SqliteApprovalStore(db)
    service2 = ApprovalService(store=reopened)
    restored = service2.create(_request())
    assert restored.state is ApprovalState.APPROVED
    assert restored.approvals == {"a", "b"}
    reopened.close()


def test_gate_blocks_before_approval_and_releases_after(tmp_path: Path) -> None:
    diff = (Path(__file__).parents[1] / "bench/fixtures/phase5/dynamic-mapper.diff").read_text(encoding="utf-8")
    root = Path(__file__).parents[1] / "fixtures" / "java-microservices"
    store = SqliteApprovalStore(tmp_path / "approvals.sqlite3")
    evaluator = ChangeEvaluator(root, approval_store=store)
    before = evaluator.evaluate(
        EvaluationRequest(diff_text=diff, repository_root=root, tests_passed=True)
    )
    assert before.decision.value == "REQUIRE_APPROVAL"
    assert gate_exit_code(before.decision.value) == 1

    service = ApprovalService(store=store)
    request = service.create(
        ApprovalRequest(
            change_context_id="ctx-approved",
            policy_revision="phase5",
            decision_fingerprint=before.decision_fingerprint,
            approvers=("coupon_platform",),
            required_cosigns=1,
        )
    )
    service.approve(request, "coupon_platform")
    after = evaluator.evaluate(
        EvaluationRequest(
            diff_text=diff,
            repository_root=root,
            tests_passed=True,
            change_context_id="ctx-approved",
        )
    )
    assert after.decision.value == "ALLOW"
    assert after.approval_state == "approved"
    assert gate_exit_code(after.decision.value) == 0
    store.close()


def test_approval_for_an_old_diff_cannot_release_a_new_diff(tmp_path: Path) -> None:
    root = Path(__file__).parents[1] / "fixtures/java-microservices"
    dynamic = (Path(__file__).parents[1] / "bench/fixtures/phase5/dynamic-mapper.diff").read_text(
        encoding="utf-8"
    )
    other = dynamic.replace("mapStatus", "mapResult")
    store = SqliteApprovalStore(tmp_path / "approvals.sqlite3")
    evaluator = ChangeEvaluator(root, approval_store=store)
    before = evaluator.evaluate(
        EvaluationRequest(diff_text=dynamic, repository_root=root, tests_passed=True)
    )
    request = ApprovalService(store=store).create(
        ApprovalRequest(
            change_context_id="ctx-replay",
            policy_revision="phase5",
            decision_fingerprint=before.decision_fingerprint,
            approvers=("coupon_platform",),
            required_cosigns=1,
        )
    )
    ApprovalService(store=store).approve(request, "coupon_platform")
    replay = evaluator.evaluate(
        EvaluationRequest(
            diff_text=other,
            repository_root=root,
            tests_passed=True,
            change_context_id="ctx-replay",
        )
    )
    assert replay.decision.value == "REQUIRE_APPROVAL"
    assert replay.approval_state == "fingerprint_mismatch"
    store.close()


def test_delegate_and_original_share_one_cosign_slot() -> None:
    service = ApprovalService()
    request = service.create(_request())
    service.delegate(request, "a", "delegate-a")
    service.approve(request, "a")
    service.approve(request, "delegate-a")
    assert request.approvals == {"a"}
    assert request.state is ApprovalState.PENDING


def test_evidence_refs_and_updated_at_are_persisted(tmp_path: Path) -> None:
    db = tmp_path / "approvals.db"
    store = SqliteApprovalStore(db)
    service = ApprovalService(store=store)
    request = service.create(_request())
    service.add_evidence(request, "test://run")
    store.close()

    reopened = SqliteApprovalStore(db)
    restored = ApprovalService(store=reopened).create(_request())
    assert restored.evidence_refs == ["test://run"]
    assert restored.updated_at is not None
    reopened.close()
