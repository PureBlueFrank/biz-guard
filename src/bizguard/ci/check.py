"""Independent-process CI check; derives a new result from the supplied diff."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.decision.v2 import DecisionInput, FindingV2, decide
from bizguard.eval.impact import changed_id_from_diff_text
from bizguard.graph.indexer import index
from bizguard.impact.service import ImpactService
from bizguard.policy.validators import validate_artifact
from bizguard.semantic.models import load_catalog
from bizguard.semantic.required_tests import select_required_tests


def evaluate(diff_text: str, base_revisions: dict[str, object] | None = None) -> dict[str, object]:
    """Recompute only from diff content; ignore any caller-provided result/cache."""
    added = "\n".join(line[1:] for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    full_content = "\n".join(
        line[1:] if line.startswith("+") and not line.startswith("+++") else line
        for line in diff_text.splitlines()
        if not line.startswith(("diff ", "index ", "---", "+++", "@@", "\\")) and not line.startswith("-")
    )
    path = next((line[4:] for line in diff_text.splitlines() if line.startswith("+++ b/")), "unknown")
    artifact = validate_artifact("published-dto-backward-compatible", full_content, path)
    confidence = artifact["confidence"]
    if not isinstance(confidence, (int, float)):
        raise ValueError("policy validator returned a non-numeric confidence")
    revisions = base_revisions or {}
    revision_hash = sha256(json.dumps(revisions, sort_keys=True).encode("utf-8")).hexdigest()
    public_change = Path(path).suffix in {".proto", ".yaml", ".yml", ".json"} or any(
        token in added.lower() for token in ("openapi", "message ", "dto", "enum ", "record ")
    )
    revision = str(revisions.get("revision", "phase3-fixture-v1"))
    impact_finding, impact_tests, impact_owners = _impact_boundary(diff_text, revision)
    required_tests = _merge_tests(_required_tests(public_change), impact_tests)
    artifact_finding = FindingV2(
        id=f"{artifact['id']}:{revision_hash[:12]}", severity=str(artifact["severity"]), effect=str(artifact["effect"]),
        remediation=str(artifact["remediation"]), confidence=float(confidence),
        violated=bool(artifact["violated"]), public_contract=public_change, required_approver="coupon_platform",
    )
    findings = [artifact_finding, *([impact_finding] if impact_finding is not None else [])]
    result = decide(
        DecisionInput(
            findings=findings,
            required_tests=[str(item["id"]) for item in required_tests],
            tests_passed=True,
            owners=sorted({"coupon_platform", *impact_owners}),
        )
    )
    payload = result.model_dump(mode="json")
    payload["required_tests"] = required_tests
    payload["audit_event_id"] = "ci-recomputed"
    payload["base_revisions_sha256"] = revision_hash
    return payload


def _impact_boundary(
    diff_text: str,
    revision: str,
) -> tuple[FindingV2 | None, list[dict[str, object]], list[str]]:
    """Return a conservative finding when the fixture graph ends at an unknown boundary."""
    repositories = Path(__file__).parents[3] / "fixtures/java-microservices"
    snapshot = index(repositories, revision)
    try:
        changed_symbol = changed_id_from_diff_text(snapshot, diff_text, "ci diff")
    except ValueError:
        return None, [], []
    report = ImpactService(repositories).analyze(
        changed_symbol,
        revision,
        capability=None,
        diff_text=diff_text,
    )
    if not report.unknown_boundary:
        return None, report.required_tests, []
    reason = report.unknown_reason or "UNKNOWN_BOUNDARY"
    approver = report.required_approvers[0] if report.required_approvers else None
    finding = FindingV2(
        id=f"impact:{reason}:{changed_symbol}",
        severity="high",
        effect="cross-service impact path ends at an unknown boundary",
        remediation="obtain owner approval and attach boundary evidence",
        required_approver=approver,
        confidence=1.0,
        critical_unknown=True,
    )
    return finding, report.required_tests, report.required_approvers


def _merge_tests(
    first: list[dict[str, object]],
    second: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Merge required-test payloads by stable test ID."""
    merged: dict[str, dict[str, object]] = {}
    for item in [*first, *second]:
        merged[str(item["id"])] = item
    return [merged[key] for key in sorted(merged)]


def _required_tests(public_change: bool) -> list[dict[str, object]]:
    if not public_change:
        return []
    catalog = load_catalog(Path(__file__).parents[1] / "semantic" / "catalog.yaml")
    return [
        item.model_dump()
        for item in select_required_tests(catalog, "dto_field_contract", "coupon-dto-field-compatibility")
    ]


def main() -> int:
    """Evaluate a diff and print the CI decision."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", type=Path, required=True)
    parser.add_argument("--base-revisions", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.diff.is_file() or not args.base_revisions.is_file():
        return 2
    raw_revisions = yaml.safe_load(args.base_revisions.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_revisions, dict) or not all(isinstance(key, str) for key in raw_revisions):
        return 2
    result = evaluate(args.diff.read_text(encoding="utf-8"), raw_revisions)
    print(json.dumps(result, sort_keys=True) if args.json else result["decision"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
