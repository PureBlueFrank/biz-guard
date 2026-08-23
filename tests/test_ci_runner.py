"""Trusted CI runner executes catalog tests and binds their evidence."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml  # type: ignore[import-untyped]

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import EvaluationRequest
from bizguard.ci.runner import run_gate
from bizguard.ci.runner import _execute_test
from bizguard.semantic.models import CatalogRequiredTest
from bizguard.workflow.approval import ApprovalRequest, ApprovalService
from bizguard.workflow.store import SqliteApprovalStore


ROOT = Path(__file__).parents[1]


def _run(diff: Path, evidence: Path, test_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "bizguard.ci.runner",
            "--diff",
            str(diff),
            "--base-revisions",
            str(ROOT / "bench/fixtures/phase3-revisions.yaml"),
            "--repository-root",
            str(ROOT / "fixtures/java-microservices"),
            "--test-root",
            str(test_root or ROOT / "fixtures/java-microservices"),
            "--test-evidence-out",
            str(evidence),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_runner_executes_required_test_and_releases_allow(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    completed = _run(ROOT / "bench/fixtures/phase3/dto-status.diff", evidence)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["decision"] == "ALLOW"
    records = json.loads(evidence.read_text(encoding="utf-8"))
    assert records == [
        {
            "evidence_uri": records[0]["evidence_uri"],
            "passed": True,
            "revision": "phase3-fixture-v1",
            "test_id": "coupon-ledger-audit-test",
        }
    ]
    assert records[0]["evidence_uri"].startswith("ci://required-test/")


def test_runner_missing_test_repository_never_allows(tmp_path: Path) -> None:
    test_root = tmp_path / "repositories"
    test_root.mkdir()
    evidence = tmp_path / "evidence.json"
    completed = _run(ROOT / "bench/fixtures/phase3/dto-status.diff", evidence, test_root)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["decision"] == "ALLOW_WITH_TESTS"
    assert json.loads(evidence.read_text(encoding="utf-8"))[0]["passed"] is False


def test_runner_consumes_matching_persisted_approval(tmp_path: Path) -> None:
    root = ROOT / "fixtures/java-microservices"
    diff_text = (ROOT / "bench/fixtures/phase5/dynamic-mapper.diff").read_text(
        encoding="utf-8"
    )
    revisions = yaml.safe_load(
        (ROOT / "bench/fixtures/phase3-revisions.yaml").read_text(encoding="utf-8")
    )
    pending = ChangeEvaluator(root).evaluate(
        EvaluationRequest(
            diff_text=diff_text,
            repository_root=root,
            base_revisions=revisions,
            tests_passed=True,
        )
    )
    store = SqliteApprovalStore(tmp_path / "approvals.sqlite3")
    request = ApprovalService(store=store).create(
        ApprovalRequest(
            change_context_id="ctx-runner-approved",
            policy_revision="phase5",
            decision_fingerprint=pending.decision_fingerprint,
            approvers=("coupon_platform",),
            required_cosigns=1,
        )
    )
    ApprovalService(store=store).approve(request, "coupon_platform")
    result, evidence = run_gate(
        diff_text,
        revisions,
        root,
        root,
        change_context_id="ctx-runner-approved",
        approval_store=store,
    )
    assert result["decision"] == "ALLOW"
    assert result["approval_state"] == "approved"
    assert evidence and all(item.passed for item in evidence)
    store.close()


def test_required_test_process_does_not_inherit_bizguard_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "service"
    repository.mkdir()
    script = repository / "verify.sh"
    script.write_text(
        "#!/bin/sh\n[ -z \"${BIZGUARD_API_TOKEN:-}\" ]\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("BIZGUARD_API_TOKEN", "must-not-leak")
    evidence = _execute_test(
        CatalogRequiredTest(
            id="secret-isolation",
            capability="test",
            owner="test",
            policy="test",
            command="./verify.sh",
            repository="service",
        ),
        tmp_path,
        "revision",
        30,
    )
    assert evidence.passed is True


def test_runner_uses_deployment_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = yaml.safe_load(
        (ROOT / "src/bizguard/semantic/catalog.yaml").read_text(encoding="utf-8")
    )
    required_test = next(
        item for item in catalog["required_tests"] if item["id"] == "coupon-ledger-audit-test"
    )
    required_test["command"] = "false"
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
    monkeypatch.setenv("BIZGUARD_CATALOG_PATH", str(catalog_path))
    revisions = yaml.safe_load(
        (ROOT / "bench/fixtures/phase3-revisions.yaml").read_text(encoding="utf-8")
    )
    result, evidence = run_gate(
        (ROOT / "bench/fixtures/phase3/dto-status.diff").read_text(encoding="utf-8"),
        revisions,
        ROOT / "fixtures/java-microservices",
        ROOT / "fixtures/java-microservices",
    )
    assert result["decision"] == "ALLOW_WITH_TESTS"
    assert len(evidence) == 1
    assert evidence[0].passed is False
