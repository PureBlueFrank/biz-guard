"""Independent-process CI check; derives a new result from the supplied diff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import EvaluationRequest


_DEFAULT_REPOSITORY_ROOT = Path(__file__).parents[3] / "fixtures" / "java-microservices"


def evaluate(
    diff_text: str,
    base_revisions: dict[str, object] | None = None,
    repository_root: Path | None = None,
) -> dict[str, object]:
    """Recompute only from diff content; ignore any caller-provided result/cache."""
    root = repository_root or _DEFAULT_REPOSITORY_ROOT
    decision = ChangeEvaluator(root).evaluate(
        EvaluationRequest(
            diff_text=diff_text,
            repository_root=root,
            base_revisions=base_revisions or {},
        )
    )
    payload = decision.model_dump(mode="json")
    payload["audit_event_id"] = "ci-recomputed"
    return payload


def gate_exit_code(
    decision: str, *, tests_complete: bool = False, approved: bool = False
) -> int:
    """Map a canonical decision to the CI gate exit code.

    ``ALLOW`` and evidence-satisfied states return 0; everything that forbids
    an automatic merge returns 1; an unrecognized decision returns 2 so the
    gate never silently passes.
    """
    if decision == "ALLOW":
        return 0
    if decision == "ALLOW_WITH_TESTS":
        return 0 if tests_complete else 1
    if decision == "REQUIRE_APPROVAL":
        return 0 if approved else 1
    if decision == "BLOCK":
        return 1
    return 2


def main() -> int:
    """Evaluate a diff, print the CI decision, and return the gate exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", type=Path, required=True)
    parser.add_argument("--base-revisions", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.diff.is_file() or not args.base_revisions.is_file():
        return 2
    raw_revisions = yaml.safe_load(args.base_revisions.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_revisions, dict) or not all(isinstance(key, str) for key in raw_revisions):
        return 2
    result = evaluate(args.diff.read_text(encoding="utf-8"), raw_revisions, args.repository_root)
    print(json.dumps(result, sort_keys=True) if args.json else result["decision"])
    return gate_exit_code(str(result["decision"]))


if __name__ == "__main__":
    raise SystemExit(main())
