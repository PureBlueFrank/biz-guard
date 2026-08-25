"""Persistent approval workflow: recovery, idempotency, and gate release."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import shutil

import pytest

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import EvaluationRequest
from bizguard.ci.check import gate_exit_code
from bizguard.graph.indexer import content_digest
from bizguard.knowledge.ingest import knowledge_content_digest
from bizguard.production import GovernancePaths
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


def test_repository_content_change_invalidates_prepared_context_and_approval(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repositories"
    shutil.copytree(Path(__file__).parents[1] / "fixtures/java-microservices", root)
    diff = (Path(__file__).parents[1] / "bench/fixtures/phase5/dynamic-mapper.diff").read_text(
        encoding="utf-8"
    )
    prepared_digest = content_digest(root)
    store = SqliteApprovalStore(tmp_path / "approvals.sqlite3")
    evaluator = ChangeEvaluator(root, approval_store=store)
    request = EvaluationRequest(
        diff_text=diff,
        repository_root=root,
        tests_passed=True,
        change_context_id="ctx-content-bound",
        prepared_graph_content_digest=prepared_digest,
    )
    before = evaluator.evaluate(request)
    approval = ApprovalService(store=store).create(
        ApprovalRequest(
            change_context_id="ctx-content-bound",
            policy_revision="phase5",
            decision_fingerprint=before.decision_fingerprint,
            approvers=("coupon_platform",),
            required_cosigns=1,
        )
    )
    ApprovalService(store=store).approve(approval, "coupon_platform")

    source = root / "coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n// repository changed\n",
        encoding="utf-8",
    )
    after = evaluator.evaluate(request)

    assert before.decision_fingerprint != after.decision_fingerprint
    assert after.approval_state == "fingerprint_mismatch"
    assert any(item.id == "context:STALE_CONTEXT" for item in after.findings)
    assert after.decision.value == "REQUIRE_APPROVAL"
    store.close()


def test_exact_submitted_diff_is_excluded_from_stale_context_detection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repositories"
    shutil.copytree(Path(__file__).parents[1] / "fixtures/java-microservices", root)
    prepared_digest = content_digest(root)
    diff = """\
diff --git a/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java b/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java
--- a/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java
+++ b/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java
@@ -3,1 +3,2 @@
 public record CouponResponse(String redemptionId, String status) {}
+// harmless target change
"""
    request = EvaluationRequest(
        diff_text=diff,
        repository_root=root,
        tests_passed=True,
        prepared_graph_content_digest=prepared_digest,
    )
    evaluator = ChangeEvaluator(root)
    evaluated_before_apply = evaluator.evaluate(request)
    target = root / "coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java"
    target.write_text(
        target.read_text(encoding="utf-8") + "// harmless target change\n",
        encoding="utf-8",
    )

    evaluated_after_apply = evaluator.evaluate(request)

    assert not any(
        item.id == "context:STALE_CONTEXT" for item in evaluated_after_apply.findings
    )
    assert (
        evaluated_before_apply.decision_fingerprint
        == evaluated_after_apply.decision_fingerprint
    )

    extra = root / "coupon-core/src/main/java/com/bizguard/coupon/api/CouponRequest.java"
    extra.write_text(
        extra.read_text(encoding="utf-8") + "// undeclared extra change\n",
        encoding="utf-8",
    )
    drifted = evaluator.evaluate(request)
    assert any(item.id == "context:STALE_CONTEXT" for item in drifted.findings)


def test_knowledge_content_change_invalidates_prepared_context(
    tmp_path: Path,
) -> None:
    knowledge = tmp_path / "knowledge"
    shutil.copytree(Path(__file__).parents[1] / "knowledge/published", knowledge)
    governance = replace(GovernancePaths.from_env(), knowledge=knowledge)
    root = Path(__file__).parents[1] / "fixtures/java-microservices"
    diff = (Path(__file__).parents[1] / "bench/fixtures/phase5/dynamic-mapper.diff").read_text(
        encoding="utf-8"
    )
    request = EvaluationRequest(
        diff_text=diff,
        repository_root=root,
        tests_passed=True,
        prepared_knowledge_content_digest=knowledge_content_digest(knowledge),
    )
    evaluator = ChangeEvaluator(root, governance=governance)
    before = evaluator.evaluate(request)
    entry = knowledge / "global-logging.md"
    entry.write_text(entry.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")

    after = evaluator.evaluate(request)

    assert before.decision_fingerprint != after.decision_fingerprint
    assert any(item.id == "context:STALE_CONTEXT" for item in after.findings)
    assert after.decision.value == "REQUIRE_APPROVAL"


def test_unrelated_cosigner_cannot_satisfy_required_owner(tmp_path: Path) -> None:
    root = Path(__file__).parents[1] / "fixtures/java-microservices"
    diff = (Path(__file__).parents[1] / "bench/fixtures/phase5/dynamic-mapper.diff").read_text(
        encoding="utf-8"
    )
    store = SqliteApprovalStore(tmp_path / "approvals.sqlite3")
    evaluator = ChangeEvaluator(root, approval_store=store)
    pending = evaluator.evaluate(
        EvaluationRequest(diff_text=diff, repository_root=root, tests_passed=True)
    )
    request = ApprovalService(store=store).create(
        ApprovalRequest(
            change_context_id="ctx-wrong-cosigner",
            policy_revision="phase5",
            decision_fingerprint=pending.decision_fingerprint,
            approvers=("coupon_platform", "engineering"),
            required_cosigns=1,
        )
    )
    ApprovalService(store=store).approve(request, "engineering")
    decision = evaluator.evaluate(
        EvaluationRequest(
            diff_text=diff,
            repository_root=root,
            tests_passed=True,
            change_context_id="ctx-wrong-cosigner",
        )
    )
    assert decision.decision.value == "REQUIRE_APPROVAL"
    assert decision.approval_state == "approver_mismatch"
    store.close()


def test_forged_approved_state_with_too_few_cosigns_is_rejected(tmp_path: Path) -> None:
    root = Path(__file__).parents[1] / "fixtures/java-microservices"
    diff = (Path(__file__).parents[1] / "bench/fixtures/phase5/dynamic-mapper.diff").read_text(
        encoding="utf-8"
    )
    store = SqliteApprovalStore(tmp_path / "approvals.sqlite3")
    evaluator = ChangeEvaluator(root, approval_store=store)
    pending = evaluator.evaluate(
        EvaluationRequest(diff_text=diff, repository_root=root, tests_passed=True)
    )
    forged = ApprovalRequest(
        change_context_id="ctx-forged-cosigns",
        policy_revision="phase5",
        decision_fingerprint=pending.decision_fingerprint,
        approvers=("coupon_platform", "engineering"),
        required_cosigns=2,
        state=ApprovalState.APPROVED,
        approvals={"coupon_platform"},
    )
    store.put(
        forged.change_context_id,
        forged.policy_revision,
        "coupon_platform,engineering@" + forged.decision_fingerprint,
        forged.model_dump_json(),
        "now",
    )
    decision = evaluator.evaluate(
        EvaluationRequest(
            diff_text=diff,
            repository_root=root,
            tests_passed=True,
            change_context_id=forged.change_context_id,
        )
    )
    assert decision.decision.value == "REQUIRE_APPROVAL"
    assert decision.approval_state == "cosign_mismatch"
    store.close()


def test_decision_exposes_machine_actionable_follow_up() -> None:
    root = Path(__file__).parents[1] / "fixtures/java-microservices"
    diff = (Path(__file__).parents[1] / "bench/fixtures/phase5/dynamic-mapper.diff").read_text(
        encoding="utf-8"
    )
    decision = ChangeEvaluator(root).evaluate(
        EvaluationRequest(diff_text=diff, repository_root=root, tests_passed=True)
    )
    action = decision.next_actions[0]
    assert action.tool == "prepare_change"
    assert action.inputs["task"]
    assert action.inputs["repos"] == ["coupon-core"]
    assert action.inputs["base_revisions"]


def test_removed_prepared_test_is_a_conservative_context_drift() -> None:
    root = Path(__file__).parents[1] / "fixtures/java-microservices"
    diff = (
        Path(__file__).parents[1] / "bench/fixtures/phase5/cross-service-dto-breaking.diff"
    ).read_text(encoding="utf-8")
    decision = ChangeEvaluator(root).evaluate(
        EvaluationRequest(
            diff_text=diff,
            repository_root=root,
            tests_passed=True,
            prepared_required_tests=["removed-governed-test"],
            prepared_required_approvers=[],
        )
    )
    assert any(item.id == "context:CONTEXT_DRIFT" for item in decision.findings)
    assert decision.decision.value == "REQUIRE_APPROVAL"


def test_new_requirements_expand_context_without_dropping_prepared_requirements() -> None:
    root = Path(__file__).parents[1] / "fixtures/java-microservices"
    diff = """diff --git a/coupon-contract/src/main/resources/coupon.proto b/coupon-contract/src/main/resources/coupon.proto
--- a/coupon-contract/src/main/resources/coupon.proto
+++ b/coupon-contract/src/main/resources/coupon.proto
@@ -4,1 +4,1 @@
-message RedeemRequest { string coupon_code = 1; string idempotency_key = 2; }
+message RedeemRequest { string coupon_code = 1; }
"""
    decision = ChangeEvaluator(root).evaluate(
        EvaluationRequest(
            diff_text=diff,
            repository_root=root,
            tests_passed=True,
            change_context_id="ctx-prepared",
            prepared_required_tests=["coupon-public-api-test"],
            prepared_required_approvers=[],
        )
    )

    finding_ids = {item.id for item in decision.findings}
    assert "context:CONTEXT_DRIFT" not in finding_ids
    assert "context:CONTEXT_EXPANDED" in finding_ids
    assert "coupon-public-api-test" in {item.id for item in decision.required_tests}
    action = decision.next_actions[0]
    assert action.tool == "request_approval"
    assert action.inputs["approvers"] == ["coupon_platform"]
    assert action.inputs["required_cosigns"] == 1


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


def test_read_only_approval_snapshot_creates_no_sqlite_sidecars(tmp_path: Path) -> None:
    db = tmp_path / "approvals.db"
    store = SqliteApprovalStore(db)
    ApprovalService(store=store).create(_request("ctx-read-only"))
    store.close()
    files_before = {path.name for path in tmp_path.iterdir()}

    reader = SqliteApprovalStore(db, read_only=True)
    assert reader.get_by_context("ctx-read-only", "phase5") is not None
    reader.close()

    assert {path.name for path in tmp_path.iterdir()} == files_before


def test_read_only_approval_snapshot_rejects_uncheckpointed_wal(tmp_path: Path) -> None:
    db = tmp_path / "approvals.db"
    store = SqliteApprovalStore(db)
    store.close()
    Path(f"{db}-wal").touch()

    with pytest.raises(OSError, match="uncheckpointed WAL"):
        SqliteApprovalStore(db, read_only=True)


def test_store_supports_concurrent_http_workers(tmp_path: Path) -> None:
    store = SqliteApprovalStore(tmp_path / "approvals.db")
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda index: store.append_event("ctx-concurrent", f'{{"index":{index}}}'),
                range(20),
            )
        )
    assert len(store.events("ctx-concurrent")) == 20
    store.close()


def test_atomic_store_mutation_merges_stale_multi_instance_cosigns(tmp_path: Path) -> None:
    database = tmp_path / "approvals.db"
    first_store = SqliteApprovalStore(database)
    second_store = SqliteApprovalStore(database)
    request = ApprovalService(first_store).create(
        ApprovalRequest(
            change_context_id="ctx-cross-instance",
            policy_revision="production-v1",
            decision_fingerprint="b" * 64,
            approvers=("owner-a", "owner-b"),
            required_cosigns=2,
        )
    )
    stale_copy = request.model_copy(deep=True)
    ApprovalService(first_store).approve(request, "owner-a")
    ApprovalService(second_store).approve(stale_copy, "owner-b")

    restored = ApprovalService(first_store).create(request.model_copy(deep=True))
    assert restored.approvals == {"owner-a", "owner-b"}
    assert restored.state is ApprovalState.APPROVED
    assert len(first_store.events(request.change_context_id)) == 4
    first_store.close()
    second_store.close()
