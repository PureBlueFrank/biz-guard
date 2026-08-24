"""CI gate exit-code contract and subprocess verification."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml  # type: ignore[import-untyped]

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import EvaluationRequest, TestEvidence as EvidenceRecord
from bizguard.ci.check import evaluate, gate_exit_code
from bizguard.workflow.approval import ApprovalRequest, ApprovalService
from bizguard.workflow.store import SqliteApprovalStore


ROOT = Path(__file__).parents[1]
REVISIONS = ROOT / "bench" / "fixtures" / "phase3-revisions.yaml"


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("ALLOW", 0),
        ("ALLOW_WITH_TESTS", 1),
        ("REQUIRE_APPROVAL", 1),
        ("BLOCK", 1),
        ("UNKNOWN", 2),
    ],
)
def test_gate_exit_code_contract(decision: str, expected: int) -> None:
    assert gate_exit_code(decision) == expected


def _run_gate(
    diff_path: Path,
    revisions: Path | None = None,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, "-m", "bizguard.ci.check", "--diff", str(diff_path)]
    args += ["--base-revisions", str(revisions or REVISIONS)]
    args += list(extra)
    args += ["--json"]
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)


def test_block_fixture_returns_exit_one() -> None:
    diff = ROOT / "sample" / "diffs" / "diff_violation_1.diff"
    completed = _run_gate(diff)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["decision"] == "BLOCK"


def test_unapproved_approval_fixture_returns_non_zero() -> None:
    diff = ROOT / "bench" / "fixtures" / "phase5" / "dynamic-mapper.diff"
    completed = _run_gate(diff)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["decision"] == "REQUIRE_APPROVAL"


def test_missing_test_evidence_never_becomes_allow() -> None:
    diff = ROOT / "sample" / "diffs" / "diff_normal_1.diff"
    completed = _run_gate(diff)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["decision"] == "ALLOW_WITH_TESTS"


def test_ci_rejects_global_tests_complete_claim() -> None:
    diff = ROOT / "sample" / "diffs" / "diff_normal_1.diff"
    completed = _run_gate(diff, None, "--tests-complete")
    assert completed.returncode == 2
    assert "ALLOW" not in completed.stdout


def test_persisted_approval_without_test_evidence_remains_blocked(tmp_path: Path) -> None:
    diff = ROOT / "bench" / "fixtures" / "phase5" / "dynamic-mapper.diff"
    approval_db = tmp_path / "approvals.sqlite3"
    store = SqliteApprovalStore(approval_db)
    root = ROOT / "fixtures/java-microservices"
    pending = ChangeEvaluator(root).evaluate(
        EvaluationRequest(
            diff_text=diff.read_text(encoding="utf-8"),
            repository_root=root,
            base_revisions=yaml.safe_load(REVISIONS.read_text(encoding="utf-8")),
            tests_passed=True,
        )
    )
    request = ApprovalService(store=store).create(
        ApprovalRequest(
            change_context_id="ctx-ci-approved",
            policy_revision="phase5",
            decision_fingerprint=pending.decision_fingerprint,
            approvers=("coupon_platform",),
            required_cosigns=1,
        )
    )
    ApprovalService(store=store).approve(request, "coupon_platform")
    store.close()

    completed = _run_gate(
        diff,
        None,
        "--change-context-id",
        "ctx-ci-approved",
        "--approval-db",
        str(approval_db),
        "--repository-root",
        str(root),
    )
    assert completed.returncode == 1, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["decision"] == "ALLOW_WITH_TESTS"
    assert payload["approval_state"] == "approved"


def test_multi_file_shadow_finding_in_second_file_is_observable(tmp_path: Path) -> None:
    first = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,1 @@
-# title
+# title v2
"""
    second = """\
diff --git a/coupon-core/src/main/resources/db/V2__ledger.sql b/coupon-core/src/main/resources/db/V2__ledger.sql
--- a/coupon-core/src/main/resources/db/V2__ledger.sql
+++ b/coupon-core/src/main/resources/db/V2__ledger.sql
@@ -1,1 +1,1 @@
-UPDATE ledger SET status='SUCCESS';
+UPDATE ledger SET status='FAILED';
"""
    diff_path = tmp_path / "multi.diff"
    diff_path.write_text(first + second, encoding="utf-8")
    completed = _run_gate(diff_path)
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["decision"] == "ALLOW"
    assert payload["shadow_findings"]


def test_missing_base_revisions_returns_exit_two(tmp_path: Path) -> None:
    diff_path = tmp_path / "input.diff"
    diff_path.write_text("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-a\n+b\n", encoding="utf-8")
    completed = _run_gate(diff_path, revisions=tmp_path / "missing.yaml")
    assert completed.returncode == 2


def test_unparsable_diff_never_passes(tmp_path: Path) -> None:
    diff_path = tmp_path / "bad.diff"
    diff_path.write_text("this is not a unified diff\n", encoding="utf-8")
    completed = _run_gate(diff_path)
    assert completed.returncode != 0


def test_untrusted_test_evidence_argument_is_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("[]", encoding="utf-8")
    diff = ROOT / "sample/diffs/diff_normal_1.diff"
    completed = _run_gate(diff, None, "--test-evidence", str(evidence))
    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr


def test_revision_bound_test_evidence_releases_only_the_matching_change() -> None:
    diff = ROOT / "sample/diffs/diff_normal_1.diff"
    revisions = yaml.safe_load(REVISIONS.read_text(encoding="utf-8"))
    evidence = [
        EvidenceRecord(
            test_id=test_id,
            passed=True,
            revision="phase3-fixture-v1",
            evidence_uri=f"ci://run/{test_id}",
        )
        for test_id in (
            "coupon-core-redeem-idempotency-test",
            "merchant-service-coupon-status-test",
        )
    ]
    matching = evaluate(diff.read_text(encoding="utf-8"), revisions, test_evidence=evidence)
    assert matching["decision"] == "ALLOW"

    evidence[0] = evidence[0].model_copy(update={"revision": "different-revision"})
    stale = evaluate(diff.read_text(encoding="utf-8"), revisions, test_evidence=evidence)
    assert stale["decision"] == "ALLOW_WITH_TESTS"


def test_ci_writes_metadata_only_audit_record(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    diff = ROOT / "sample/diffs/diff_normal_1.diff"
    completed = _run_gate(
        diff,
        None,
        "--audit-log",
        str(audit),
    )
    assert completed.returncode == 1
    record = json.loads(audit.read_text(encoding="utf-8"))
    assert record["event"] == "ci_decision"
    assert record["details"]["decision"] == "ALLOW_WITH_TESTS"
    assert "diff_text" not in record["details"]


def test_unresolved_java_capability_fails_closed_without_traceback() -> None:
    diff = ROOT / "bench/fixtures/phase3/db-idempotency.diff"
    completed = _run_gate(
        diff,
        None,
        "--repository-root",
        str(ROOT / "fixtures/java-microservices"),
    )
    assert completed.returncode == 1
    assert "Traceback" not in completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["decision"] == "REQUIRE_APPROVAL"
    assert any(item["id"].startswith("impact:CAPABILITY_UNRESOLVED:") for item in payload["findings"])
