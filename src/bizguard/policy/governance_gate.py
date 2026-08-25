"""Fail closed when a registry promotion lacks signed calibration evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.policy.calibration import verify_bundle
from bizguard.policy.lifecycle import PolicyMode
from bizguard.policy.registry import PolicyDefinition, load_registry


def verify_registry_change(
    base_registry: Path,
    current_registry: Path,
    gates: Path,
    public_key: Path,
    bundle_directory: Path,
) -> list[str]:
    """Return governance violations for unsafe additions, mutations, or promotions."""
    before = {policy.id: policy for policy in _load_trusted_base(base_registry)}
    after = {policy.id: policy for policy in load_registry(current_registry)}
    reasons: list[str] = []
    removed = sorted(set(before) - set(after))
    if removed:
        reasons.append("registered policies cannot be deleted: " + ", ".join(removed))
    for policy_id, current in after.items():
        previous = before.get(policy_id)
        if previous is None:
            if current.mode not in {PolicyMode.DRAFT, PolicyMode.SHADOW}:
                reasons.append(f"new policy must start in draft or shadow: {policy_id}")
            continue
        if _definition_changed(previous, current) and current.mode not in {
            PolicyMode.DRAFT,
            PolicyMode.SHADOW,
        }:
            reasons.append(f"materially changed policy must return to shadow: {policy_id}")
        before_rank = list(PolicyMode).index(previous.mode)
        after_rank = list(PolicyMode).index(current.mode)
        if after_rank <= before_rank:
            continue
        if after_rank != before_rank + 1:
            reasons.append(f"policy promotion must advance one stage: {policy_id}")
            continue
        bundle = bundle_directory / f"{policy_id}.json"
        if not bundle.is_file():
            reasons.append(f"signed calibration bundle is missing: {policy_id}")
            continue
        # The signed evidence describes the transition from the trusted base mode.
        # Validating it against the already-promoted registry would reject every
        # otherwise valid promotion because verify_bundle intentionally binds
        # from_mode to the supplied registry.
        report = verify_bundle(bundle, base_registry, gates, public_key)
        if (
            not report.eligible
            or report.from_mode is not previous.mode
            or report.target_mode is not current.mode
        ):
            detail = ", ".join(report.reasons) or "bundle lifecycle does not match registry diff"
            reasons.append(f"policy calibration gate failed for {policy_id}: {detail}")
    return reasons


def _definition_changed(before: PolicyDefinition, after: PolicyDefinition) -> bool:
    fields = (
        "validator",
        "scope",
        "severity",
        "owner",
        "required_tests",
        "file_patterns",
        "precision",
    )
    return any(getattr(before, field) != getattr(after, field) for field in fields)


def _load_trusted_base(path: Path) -> list[PolicyDefinition]:
    """Read the one pre-file-pattern registry schema used before this gate existed."""
    try:
        return load_registry(path)
    except ValueError as original:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        records = payload.get("policies") if isinstance(payload, dict) else None
        if not isinstance(records, list) or not records:
            raise original
        legacy_patterns = {
            "proto_openapi": [
                "**/*.proto",
                "*.proto",
                "**/*.yaml",
                "*.yaml",
                "**/*.yml",
                "*.yml",
                "**/*.json",
                "*.json",
            ],
            "sql_transaction": ["**/*.sql", "*.sql"],
            "schema_version": ["**/*.avsc", "*.avsc", "**/*.schema", "*.schema"],
        }
        migrated: list[PolicyDefinition] = []
        for raw in records:
            if not isinstance(raw, dict) or "file_patterns" in raw:
                raise original
            validator = raw.get("validator")
            if validator not in legacy_patterns:
                raise original
            migrated.append(
                PolicyDefinition.model_validate(
                    {**raw, "file_patterns": legacy_patterns[str(validator)]}
                )
            )
        return migrated


def main(argv: list[str] | None = None) -> int:
    """Run the policy registry governance gate."""
    parser = argparse.ArgumentParser(description="Verify BizGuard policy registry governance")
    parser.add_argument("--base-registry", type=Path, required=True)
    parser.add_argument("--current-registry", type=Path, required=True)
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--bundle-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        reasons = verify_registry_change(
            arguments.base_registry,
            arguments.current_registry,
            arguments.gates,
            arguments.public_key,
            arguments.bundle_directory,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": not reasons, "reasons": reasons}, ensure_ascii=False))
    return 0 if not reasons else 1


if __name__ == "__main__":
    raise SystemExit(main())
