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


def test_gate_blocks_before_approval_and_releases_after() -> None:
    diff = (Path(__file__).parents[1] / "bench/fixtures/phase5/dynamic-mapper.diff").read_text(encoding="utf-8")
    root = Path(__file__).parents[1] / "fixtures" / "java-microservices"
    decision = ChangeEvaluator(root).evaluate(
        EvaluationRequest(diff_text=diff, repository_root=root)
    )
    assert decision.decision.value == "REQUIRE_APPROVAL"
    assert gate_exit_code(decision.decision.value) == 1
    assert gate_exit_code(decision.decision.value, approved=True) == 0


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
