"""Signed production calibration evidence cannot be forged or under-sampled."""

from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from bizguard.policy.calibration import (
    CalibrationBundle,
    CalibrationObservation,
    OwnerApproval,
    verify_bundle,
)
from bizguard.policy.lifecycle import PolicyMode


ROOT = Path(__file__).parents[1]


def _sign(private_key: Ed25519PrivateKey, record: object) -> None:
    payload = record.model_dump(mode="json", exclude={"signature"})  # type: ignore[attr-defined]
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    record.signature = b64encode(private_key.sign(canonical)).decode("ascii")  # type: ignore[attr-defined]


def _bundle(private_key: Ed25519PrivateKey) -> CalibrationBundle:
    started = datetime(2026, 7, 1, tzinfo=timezone.utc)
    observations = [
        CalibrationObservation(
            signature="pending",
            sample_id=f"sample-{index}",
            decision_fingerprint=f"{index:064x}",
            observed_at=started + timedelta(hours=index),
            source_revision=f"revision-{index}",
            expected_violation=index < 10,
            policy_triggered=index < 10,
            reviewer="calibration-reviewer",
            evidence_uri=f"audit://calibration/sample-{index}",
        )
        for index in range(30)
    ]
    for observation in observations:
        _sign(private_key, observation)
    approval = OwnerApproval(
        signature="pending",
        policy_id="redeem-ledger-consistency",
        from_mode=PolicyMode.SHADOW,
        target_mode=PolicyMode.WARNING,
        owner="coupon_platform",
        approved_at=started + timedelta(days=2),
        evidence_uri="audit://calibration/owner-approval",
    )
    _sign(private_key, approval)
    return CalibrationBundle(
        schema_version=1,
        policy_id="redeem-ledger-consistency",
        from_mode=PolicyMode.SHADOW,
        target_mode=PolicyMode.WARNING,
        window_started_at=started,
        window_ended_at=started + timedelta(days=3),
        observations=observations,
        owner_approval=approval,
    )


def _write_inputs(
    tmp_path: Path, private_key: Ed25519PrivateKey, bundle: CalibrationBundle
) -> tuple[Path, Path]:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")
    public_key_path = tmp_path / "public-key.pem"
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return bundle_path, public_key_path


def test_signed_real_sample_bundle_meets_shadow_to_warning_gate(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    bundle_path, public_key_path = _write_inputs(tmp_path, private_key, _bundle(private_key))
    report = verify_bundle(
        bundle_path,
        ROOT / "policy/phase5-registry.yaml",
        ROOT / "policy/calibration-gates.yaml",
        public_key_path,
    )
    assert report.eligible
    assert report.sample_count == 30
    assert report.false_positive_rate == report.false_negative_rate == 0.0


def test_tampered_calibration_observation_is_rejected(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    bundle = _bundle(private_key)
    bundle.observations[0].policy_triggered = False
    bundle_path, public_key_path = _write_inputs(tmp_path, private_key, bundle)
    with pytest.raises(ValueError, match="signature is invalid"):
        verify_bundle(
            bundle_path,
            ROOT / "policy/phase5-registry.yaml",
            ROOT / "policy/calibration-gates.yaml",
            public_key_path,
        )


def test_blocking_rejects_medium_precision_policy_even_with_signed_samples(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    bundle = _bundle(private_key)
    bundle.policy_id = "published-dto-backward-compatible"
    bundle.from_mode = PolicyMode.WARNING
    bundle.target_mode = PolicyMode.BLOCKING
    bundle.owner_approval.policy_id = bundle.policy_id
    bundle.owner_approval.from_mode = bundle.from_mode
    bundle.owner_approval.target_mode = bundle.target_mode
    _sign(private_key, bundle.owner_approval)
    bundle_path, public_key_path = _write_inputs(tmp_path, private_key, bundle)
    report = verify_bundle(
        bundle_path,
        ROOT / "policy/phase5-registry.yaml",
        ROOT / "policy/calibration-gates.yaml",
        public_key_path,
    )
    assert not report.eligible
    assert "high-precision" in " ".join(report.reasons)
