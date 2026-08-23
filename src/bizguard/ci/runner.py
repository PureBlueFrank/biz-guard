"""Trusted CI runner that executes required tests before recomputing a gate decision."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import subprocess
from collections.abc import Sequence
from typing import cast

import yaml  # type: ignore[import-untyped]

from bizguard.change.models import TestEvidence
from bizguard.ci.check import evaluate, gate_exit_code
from bizguard.observability import audit_json
from bizguard.semantic.models import CatalogRequiredTest
from bizguard.workflow.store import SqliteApprovalStore


def _repository_directory(test_root: Path, repository: str) -> Path:
    """Resolve a catalog repository without allowing traversal outside the test root."""
    root = test_root.resolve()
    candidate = root if root.name == repository else (root / repository).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"test repository escapes configured root: {repository}") from exc
    if not candidate.is_dir():
        raise ValueError(f"test repository is unavailable: {repository}")
    return candidate


def _execute_test(
    test: CatalogRequiredTest,
    test_root: Path,
    revision: str,
    timeout_seconds: int,
) -> TestEvidence:
    """Run one catalog command without a shell and return metadata-only evidence."""
    try:
        directory = _repository_directory(test_root, test.repository)
        command = shlex.split(test.command)
        if not command:
            raise ValueError("required test command is empty")
        if "/" in command[0]:
            executable = (directory / command[0]).resolve()
            try:
                executable.relative_to(directory)
            except ValueError as exc:
                raise ValueError("required test executable escapes its repository") from exc
            if not executable.is_file():
                raise ValueError("required test executable is unavailable")
            command[0] = str(executable)
        allowed_environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "JAVA_HOME", "M2_HOME", "GRADLE_HOME", "LANG", "LC_ALL", "CI"}
        }
        completed = subprocess.run(
            command,
            cwd=directory,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout_seconds,
            env=allowed_environment,
        )
        passed = completed.returncode == 0
        digest_input = "\0".join(
            [test.id, revision, test.command, str(completed.returncode)]
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        passed = False
        digest_input = "\0".join([test.id, revision, test.command, type(exc).__name__, str(exc)])
    digest = sha256(digest_input.encode("utf-8")).hexdigest()
    return TestEvidence(
        test_id=test.id,
        passed=passed,
        revision=revision,
        evidence_uri=f"ci://required-test/{test.id}/{digest}",
    )


def run_gate(
    diff_text: str,
    base_revisions: dict[str, object],
    repository_root: Path,
    test_root: Path,
    *,
    timeout_seconds: int = 900,
    change_context_id: str | None = None,
    approval_store: SqliteApprovalStore | None = None,
) -> tuple[dict[str, object], list[TestEvidence]]:
    """Discover, execute, and bind every required test to the evaluated revision."""
    initial = evaluate(
        diff_text,
        base_revisions,
        repository_root,
        change_context_id=change_context_id,
        approval_store=approval_store,
    )
    raw_required = cast(list[object], initial["required_tests"])
    required = [CatalogRequiredTest.model_validate(item) for item in raw_required]
    revision = str(base_revisions.get("revision", "phase3-fixture-v1"))
    evidence = [
        _execute_test(test, test_root, revision, timeout_seconds)
        for test in required
    ]
    final = evaluate(
        diff_text,
        base_revisions,
        repository_root,
        test_evidence=evidence,
        change_context_id=change_context_id,
        approval_store=approval_store,
    )
    return final, evidence


def _error_payload(message: str) -> dict[str, object]:
    return {
        "decision": "CHECK_INCOMPLETE",
        "rationale": message,
        "findings": [],
        "required_tests": [],
        "required_approvers": [],
        "evidence": [],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run trusted required tests and print the final canonical CI decision."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", type=Path, required=True)
    parser.add_argument("--base-revisions", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--test-root", type=Path, required=True)
    parser.add_argument("--test-evidence-out", type=Path)
    parser.add_argument("--change-context-id", default=os.environ.get("BIZGUARD_CHANGE_CONTEXT_ID"))
    approval_db = os.environ.get("BIZGUARD_APPROVAL_DB") or None
    parser.add_argument("--approval-db", type=Path, default=approval_db)
    parser.add_argument("--audit-log", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if (
        not args.diff.is_file()
        or not args.base_revisions.is_file()
        or not args.repository_root.is_dir()
        or not args.test_root.is_dir()
        or args.timeout_seconds < 1
    ):
        print(json.dumps(_error_payload("invalid CI runner input"), sort_keys=True))
        return 2
    try:
        raw_revisions = yaml.safe_load(args.base_revisions.read_text(encoding="utf-8")) or {}
        if not isinstance(raw_revisions, dict) or not all(
            isinstance(key, str) for key in raw_revisions
        ):
            raise ValueError("base revisions must be a mapping")
        store = SqliteApprovalStore(args.approval_db) if args.approval_db is not None else None
        try:
            result, evidence = run_gate(
                args.diff.read_text(encoding="utf-8"),
                raw_revisions,
                args.repository_root,
                args.test_root,
                timeout_seconds=args.timeout_seconds,
                change_context_id=args.change_context_id,
                approval_store=store,
            )
        finally:
            if store is not None:
                store.close()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(json.dumps(_error_payload(str(exc)), sort_keys=True))
        return 2
    if args.test_evidence_out is not None:
        args.test_evidence_out.parent.mkdir(parents=True, exist_ok=True)
        args.test_evidence_out.write_text(
            json.dumps([item.model_dump(mode="json") for item in evidence], sort_keys=True),
            encoding="utf-8",
        )
    if args.audit_log is not None:
        audit_json(
            args.audit_log,
            args.change_context_id or "ci-unscoped",
            "ci_decision",
            {
                "decision": str(result["decision"]),
                "policy_revision": str(result["policy_revision"]),
                "required_test_count": str(len(evidence)),
                "passed_test_count": str(sum(item.passed for item in evidence)),
            },
        )
    print(json.dumps(result, sort_keys=True) if args.json else result["decision"])
    return gate_exit_code(str(result["decision"]))


if __name__ == "__main__":
    raise SystemExit(main())
