"""Independent-process CI check; derives a new result from the supplied diff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from bizguard.decision.v2 import DecisionInput, FindingV2, decide
from bizguard.policy.validators import validate_artifact


def evaluate(diff_text: str) -> dict[str, object]:
    """Recompute only from diff content; ignore any caller-provided result/cache."""
    added = "\n".join(line[1:] for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    path = next((line[4:] for line in diff_text.splitlines() if line.startswith("+++ b/")), "unknown")
    artifact = validate_artifact("published-dto-backward-compatible", added, path)
    public_change = Path(path).suffix in {".proto", ".yaml", ".yml", ".json"} or any(
        token in added.lower() for token in ("openapi", "message ", "dto", "enum ", "record ")
    )
    finding = FindingV2(
        id=str(artifact["id"]), severity=str(artifact["severity"]), effect=str(artifact["effect"]),
        remediation=str(artifact["remediation"]), confidence=cast(float, artifact["confidence"]),
        violated=bool(artifact["violated"]), public_contract=public_change, required_approver="coupon_platform",
    )
    result = decide(DecisionInput(findings=[finding], tests_passed=True, owners=["coupon_platform"], risk_score=0.2))
    payload = result.model_dump(mode="json")
    payload["audit_event_id"] = "ci-recomputed"
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", type=Path, required=True)
    parser.add_argument("--base-revisions", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.diff.is_file() or not args.base_revisions.is_file():
        return 2
    result = evaluate(args.diff.read_text(encoding="utf-8"))
    print(json.dumps(result, sort_keys=True) if args.json else result["decision"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
