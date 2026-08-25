"""Signed real-sample calibration gates for policy promotion and rollback."""

from __future__ import annotations

import argparse
from base64 import b64decode
from datetime import datetime
import json
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml  # type: ignore[import-untyped]

from bizguard.policy.lifecycle import PolicyMode
from bizguard.policy.registry import load_registry


class CalibrationGates(BaseModel):
    """Organization-owned minimum evidence required for a promotion."""

    model_config = ConfigDict(extra="forbid")

    min_samples: int = Field(ge=20)
    min_positive_samples: int = Field(ge=5)
    min_negative_samples: int = Field(ge=5)
    max_false_positive_rate: float = Field(ge=0.0, le=0.2)
    max_false_negative_rate: float = Field(ge=0.0, le=0.1)
    max_unknown_rate: float = Field(ge=0.0, le=0.2)


class SignedRecord(BaseModel):
    """Base contract for detached Ed25519-signed calibration evidence."""

    model_config = ConfigDict(extra="forbid")

    signature: str = Field(min_length=1)


class CalibrationObservation(SignedRecord):
    """One human-labelled result from a real governed change."""

    kind: Literal["observation"] = "observation"
    sample_id: str = Field(min_length=1)
    decision_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    source_revision: str = Field(min_length=7)
    expected_violation: bool | None
    policy_triggered: bool
    reviewer: str = Field(min_length=1)
    evidence_uri: str = Field(pattern=r"^(audit|https|repo)://")


class OwnerApproval(SignedRecord):
    """Authenticated owner approval bound to one exact promotion."""

    kind: Literal["owner_approval"] = "owner_approval"
    policy_id: str = Field(min_length=1)
    from_mode: PolicyMode
    target_mode: PolicyMode
    owner: str = Field(min_length=1)
    approved_at: datetime
    evidence_uri: str = Field(pattern=r"^(audit|https|repo)://")


class RollbackDrill(SignedRecord):
    """Evidence that the target mode can be rolled back operationally."""

    kind: Literal["rollback_drill"] = "rollback_drill"
    policy_id: str = Field(min_length=1)
    completed_at: datetime
    owner: str = Field(min_length=1)
    passed: bool
    recovery_seconds: int = Field(ge=0)
    evidence_uri: str = Field(pattern=r"^(audit|https|repo)://")


class CalibrationBundle(BaseModel):
    """Promotion evidence signed outside the BizGuard repository."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    policy_id: str = Field(min_length=1)
    from_mode: PolicyMode
    target_mode: PolicyMode
    window_started_at: datetime
    window_ended_at: datetime
    observations: list[CalibrationObservation]
    owner_approval: OwnerApproval
    rollback_drill: RollbackDrill | None = None

    @model_validator(mode="after")
    def valid_window(self) -> "CalibrationBundle":
        if self.window_ended_at <= self.window_started_at:
            raise ValueError("calibration window must end after it starts")
        return self


class CalibrationReport(BaseModel):
    """Machine-readable decision for a requested policy promotion."""

    policy_id: str
    from_mode: PolicyMode
    target_mode: PolicyMode
    eligible: bool
    sample_count: int
    positive_samples: int
    negative_samples: int
    unknown_samples: int
    false_positive_rate: float
    false_negative_rate: float
    unknown_rate: float
    reasons: list[str]


def load_gates(path: Path, policy_id: str) -> CalibrationGates:
    """Load one policy's protected promotion thresholds."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    policies = raw.get("policies") if isinstance(raw, dict) else None
    if not isinstance(policies, dict) or policy_id not in policies:
        raise ValueError(f"calibration gates are unavailable for policy: {policy_id}")
    return CalibrationGates.model_validate(policies[policy_id])


def validate_calibration_configuration(
    gates_path: Path, public_key_path: Path, registry_path: Path
) -> None:
    """Validate every registered policy gate and the organization verification key."""
    _load_public_key(public_key_path)
    for policy in load_registry(registry_path):
        load_gates(gates_path, policy.id)


def verify_bundle(
    bundle_path: Path,
    registry_path: Path,
    gates_path: Path,
    public_key_path: Path,
) -> CalibrationReport:
    """Verify signatures, provenance, metrics, ownership, and lifecycle transition."""
    bundle = CalibrationBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
    policy = next(
        (item for item in load_registry(registry_path) if item.id == bundle.policy_id),
        None,
    )
    if policy is None:
        raise ValueError(f"policy is not registered: {bundle.policy_id}")
    gates = load_gates(gates_path, bundle.policy_id)
    public_key = _load_public_key(public_key_path)
    records: list[SignedRecord] = [*bundle.observations, bundle.owner_approval]
    if bundle.rollback_drill is not None:
        records.append(bundle.rollback_drill)
    for record in records:
        _verify_signature(public_key, record)

    reasons: list[str] = []
    expected_target = _next_mode(bundle.from_mode)
    if policy.mode is not bundle.from_mode:
        reasons.append("registry mode does not match bundle from_mode")
    if bundle.target_mode is not expected_target:
        reasons.append("promotion must advance exactly one lifecycle stage")
    if bundle.target_mode is PolicyMode.BLOCKING and policy.precision != "high":
        reasons.append("blocking requires a high-precision deterministic validator")

    approval = bundle.owner_approval
    if (
        approval.policy_id != bundle.policy_id
        or approval.from_mode is not bundle.from_mode
        or approval.target_mode is not bundle.target_mode
        or approval.owner != policy.owner
    ):
        reasons.append("owner approval is not bound to this exact promotion")
    if not bundle.window_started_at <= approval.approved_at:
        reasons.append("owner approval predates the calibration window")

    if bundle.target_mode is PolicyMode.BLOCKING:
        drill = bundle.rollback_drill
        if (
            drill is None
            or not drill.passed
            or drill.policy_id != bundle.policy_id
            or drill.owner != policy.owner
            or drill.completed_at < bundle.window_started_at
        ):
            reasons.append("blocking requires a signed, current, successful rollback drill")

    identities = {(item.sample_id, item.decision_fingerprint) for item in bundle.observations}
    if len(identities) != len(bundle.observations):
        reasons.append("calibration observations contain duplicates")
    if any(
        not bundle.window_started_at <= item.observed_at <= bundle.window_ended_at
        for item in bundle.observations
    ):
        reasons.append("calibration observation is outside the declared window")

    known = [item for item in bundle.observations if item.expected_violation is not None]
    positive = [item for item in known if item.expected_violation is True]
    negative = [item for item in known if item.expected_violation is False]
    unknown_count = len(bundle.observations) - len(known)
    false_positives = sum(item.policy_triggered for item in negative)
    false_negatives = sum(not item.policy_triggered for item in positive)
    false_positive_rate = false_positives / len(negative) if negative else 0.0
    false_negative_rate = false_negatives / len(positive) if positive else 0.0
    unknown_rate = unknown_count / len(bundle.observations) if bundle.observations else 0.0
    if len(bundle.observations) < gates.min_samples:
        reasons.append("insufficient total calibration samples")
    if len(positive) < gates.min_positive_samples:
        reasons.append("insufficient positive calibration samples")
    if len(negative) < gates.min_negative_samples:
        reasons.append("insufficient negative calibration samples")
    if false_positive_rate > gates.max_false_positive_rate:
        reasons.append("false positive rate exceeds promotion gate")
    if false_negative_rate > gates.max_false_negative_rate:
        reasons.append("false negative rate exceeds promotion gate")
    if unknown_rate > gates.max_unknown_rate:
        reasons.append("unknown rate exceeds promotion gate")

    return CalibrationReport(
        policy_id=bundle.policy_id,
        from_mode=bundle.from_mode,
        target_mode=bundle.target_mode,
        eligible=not reasons,
        sample_count=len(bundle.observations),
        positive_samples=len(positive),
        negative_samples=len(negative),
        unknown_samples=unknown_count,
        false_positive_rate=false_positive_rate,
        false_negative_rate=false_negative_rate,
        unknown_rate=unknown_rate,
        reasons=reasons,
    )


def _next_mode(mode: PolicyMode) -> PolicyMode:
    if mode is PolicyMode.BLOCKING:
        raise ValueError("blocking policy cannot be promoted")
    modes = list(PolicyMode)
    return modes[modes.index(mode) + 1]


def _load_public_key(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("calibration public key must be Ed25519")
    return key


def _verify_signature(public_key: Ed25519PublicKey, record: SignedRecord) -> None:
    payload = record.model_dump(mode="json", exclude={"signature"})
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    try:
        signature = b64decode(record.signature, validate=True)
        public_key.verify(signature, canonical)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("calibration evidence signature is invalid") from exc


def main(argv: list[str] | None = None) -> int:
    """Verify one signed calibration bundle from CI or an operator workstation."""
    parser = argparse.ArgumentParser(description="Verify signed BizGuard policy calibration evidence")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        report = verify_bundle(
            arguments.bundle,
            arguments.registry,
            arguments.gates,
            arguments.public_key,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"eligible": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(report.model_dump_json())
    return 0 if report.eligible else 1


if __name__ == "__main__":
    raise SystemExit(main())
