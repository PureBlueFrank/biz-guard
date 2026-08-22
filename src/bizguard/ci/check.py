"""Independent-process CI check; derives a new result from the supplied diff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import EvaluationRequest, TestEvidence
from bizguard.observability import audit_json
from bizguard.workflow.store import SqliteApprovalStore


_DEFAULT_REPOSITORY_ROOT = Path(__file__).parents[3] / "fixtures" / "java-microservices"


def evaluate(
    diff_text: str,
    base_revisions: dict[str, object] | None = None,
    repository_root: Path | None = None,
    tests_passed: bool | None = None,
    test_evidence: list[TestEvidence] | None = None,
    change_context_id: str | None = None,
    approval_store: SqliteApprovalStore | None = None,
) -> dict[str, object]:
    """Recompute only from diff content; ignore any caller-provided result/cache."""
    root = repository_root or _DEFAULT_REPOSITORY_ROOT
    decision = ChangeEvaluator(root, approval_store=approval_store).evaluate(
        EvaluationRequest(
            diff_text=diff_text,
            repository_root=root,
            base_revisions=base_revisions or {},
            tests_passed=tests_passed,
            test_evidence=test_evidence or [],
            change_context_id=change_context_id,
        )
    )
    payload = decision.model_dump(mode="json")
    payload["audit_event_id"] = "ci-recomputed"
    return payload


def gate_exit_code(decision: str) -> int:
    """Map a canonical decision to the CI gate exit code.

    ``ALLOW`` and evidence-satisfied states return 0; everything that forbids
    an automatic merge returns 1; an unrecognized decision returns 2 so the
    gate never silently passes.
    """
    if decision == "ALLOW":
        return 0
    if decision in {"ALLOW_WITH_TESTS", "REQUIRE_APPROVAL", "BLOCK"}:
        return 1
    return 2


def main() -> int:
    """Evaluate a diff, print the CI decision, and return the gate exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", type=Path, required=True)
    parser.add_argument("--base-revisions", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=None)
    parser.add_argument("--tests-complete", action="store_true")
    parser.add_argument("--test-evidence", type=Path)
    parser.add_argument("--change-context-id")
    parser.add_argument("--approval-db", type=Path)
    parser.add_argument("--audit-log", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.diff.is_file() or not args.base_revisions.is_file():
        return 2
    try:
        raw_revisions = yaml.safe_load(args.base_revisions.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return 2
    if not isinstance(raw_revisions, dict) or not all(
        isinstance(key, str) for key in raw_revisions
    ):
        return 2
    evidence: list[TestEvidence] = []
    if args.test_evidence is not None:
        if not args.test_evidence.is_file():
            return 2
        try:
            raw_evidence = json.loads(args.test_evidence.read_text(encoding="utf-8"))
            if not isinstance(raw_evidence, list):
                return 2
            evidence = [TestEvidence.model_validate(item) for item in raw_evidence]
        except (OSError, json.JSONDecodeError, ValidationError):
            return 2
    store = SqliteApprovalStore(args.approval_db) if args.approval_db is not None else None
    try:
        result = evaluate(
            args.diff.read_text(encoding="utf-8"),
            raw_revisions,
            args.repository_root,
            tests_passed=True if args.tests_complete else None,
            test_evidence=evidence,
            change_context_id=args.change_context_id,
            approval_store=store,
        )
        if args.audit_log is not None:
            audit_json(
                args.audit_log,
                args.change_context_id or "ci-unscoped",
                "ci_decision",
                {
                    "decision": str(result["decision"]),
                    "policy_revision": str(result["policy_revision"]),
                },
            )
        print(json.dumps(result, sort_keys=True) if args.json else result["decision"])
        return gate_exit_code(str(result["decision"]))
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
