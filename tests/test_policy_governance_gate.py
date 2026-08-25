"""Registry lifecycle changes require signed production calibration evidence."""

from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Callable
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import yaml  # type: ignore[import-untyped]

from bizguard.policy.calibration import CalibrationObservation, OwnerApproval
from bizguard.policy.governance_gate import verify_registry_change


ROOT = Path(__file__).parents[1]


def _registry(tmp_path: Path, mutate: Callable[[list[dict[str, Any]]], None]) -> Path:
    payload = yaml.safe_load((ROOT / "policy/phase5-registry.yaml").read_text(encoding="utf-8"))
    mutate(payload["policies"])
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _verify(
    current: Path,
    tmp_path: Path,
    *,
    public_key: Path | None = None,
    bundle_directory: Path | None = None,
) -> list[str]:
    return verify_registry_change(
        ROOT / "policy/phase5-registry.yaml",
        current,
        ROOT / "policy/calibration-gates.yaml",
        public_key or ROOT / "policy/calibration-public-key.pem",
        bundle_directory or tmp_path / "calibration",
    )


def _sign(private_key: Ed25519PrivateKey, record: dict[str, Any]) -> None:
    if record["kind"] == "observation":
        model: CalibrationObservation | OwnerApproval = CalibrationObservation.model_validate(
            {**record, "signature": "pending"}
        )
    else:
        model = OwnerApproval.model_validate({**record, "signature": "pending"})
    payload = model.model_dump(mode="json", exclude={"signature"})
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    record["signature"] = b64encode(private_key.sign(canonical)).decode("ascii")


def _promotion_evidence(tmp_path: Path) -> tuple[Path, Path]:
    private_key = Ed25519PrivateKey.generate()
    public_key = tmp_path / "public-key.pem"
    public_key.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    observations: list[dict[str, Any]] = []
    for index in range(30):
        observation: dict[str, Any] = {
            "kind": "observation",
            "sample_id": f"sample-{index}",
            "decision_fingerprint": f"{index:064x}",
            "observed_at": (started + timedelta(hours=index)).isoformat(),
            "source_revision": f"revision-{index}",
            "expected_violation": index < 10,
            "policy_triggered": index < 10,
            "reviewer": "calibration-reviewer",
            "evidence_uri": f"audit://calibration/sample-{index}",
        }
        _sign(private_key, observation)
        observations.append(observation)
    owner_approval: dict[str, Any] = {
        "kind": "owner_approval",
        "policy_id": "redeem-ledger-consistency",
        "from_mode": "shadow",
        "target_mode": "warning",
        "owner": "coupon_platform",
        "approved_at": (started + timedelta(days=2)).isoformat(),
        "evidence_uri": "audit://calibration/owner-approval",
    }
    _sign(private_key, owner_approval)
    bundle_directory = tmp_path / "calibration"
    bundle_directory.mkdir()
    (bundle_directory / "redeem-ledger-consistency.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "policy_id": "redeem-ledger-consistency",
                "from_mode": "shadow",
                "target_mode": "warning",
                "window_started_at": started.isoformat(),
                "window_ended_at": (started + timedelta(days=3)).isoformat(),
                "observations": observations,
                "owner_approval": owner_approval,
                "rollback_drill": None,
            }
        ),
        encoding="utf-8",
    )
    return public_key, bundle_directory


def test_unchanged_registry_needs_no_calibration_bundle(tmp_path: Path) -> None:
    assert _verify(ROOT / "policy/phase5-registry.yaml", tmp_path) == []


def test_trusted_legacy_registry_without_file_patterns_can_bootstrap(tmp_path: Path) -> None:
    payload = yaml.safe_load((ROOT / "policy/phase5-registry.yaml").read_text(encoding="utf-8"))
    for policy in payload["policies"]:
        policy.pop("file_patterns")
    base = tmp_path / "legacy-registry.yaml"
    base.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    assert verify_registry_change(
        base,
        ROOT / "policy/phase5-registry.yaml",
        ROOT / "policy/calibration-gates.yaml",
        ROOT / "policy/calibration-public-key.pem",
        tmp_path / "calibration",
    ) == []


def test_promotion_without_signed_bundle_is_blocked(tmp_path: Path) -> None:
    current = _registry(tmp_path, lambda policies: policies[0].update({"mode": "warning"}))
    reasons = _verify(current, tmp_path)
    assert reasons == ["signed calibration bundle is missing: redeem-ledger-consistency"]


def test_one_stage_promotion_with_signed_calibration_bundle_passes(tmp_path: Path) -> None:
    current = _registry(tmp_path, lambda policies: policies[0].update({"mode": "warning"}))
    public_key, bundle_directory = _promotion_evidence(tmp_path)
    assert _verify(
        current,
        tmp_path,
        public_key=public_key,
        bundle_directory=bundle_directory,
    ) == []


def test_new_policy_cannot_start_in_blocking(tmp_path: Path) -> None:
    def add_policy(policies: list[dict[str, object]]) -> None:
        policies.append(
            {
                "id": "unsafe-new-policy",
                "validator": "schema_version",
                "scope": "global",
                "severity": "critical",
                "owner": "platform",
                "remediation": "fix",
                "file_patterns": ["**/*.schema"],
                "mode": "blocking",
                "precision": "high",
            }
        )

    reasons = _verify(_registry(tmp_path, add_policy), tmp_path)
    assert reasons == ["new policy must start in draft or shadow: unsafe-new-policy"]


def test_material_policy_change_returns_to_shadow(tmp_path: Path) -> None:
    current = _registry(
        tmp_path,
        lambda policies: policies[1].update({"owner": "different-owner"}),
    )
    assert _verify(current, tmp_path) == [
        "materially changed policy must return to shadow: published-dto-backward-compatible"
    ]
