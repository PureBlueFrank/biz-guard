"""Ground truth for the version-two domain contracts."""

import pytest
from pydantic import ValidationError

from bizguard.domain.enums import ArtifactStatus, CheckStatus, DecisionState
from bizguard.domain.models import ChangeContext, ChangedArtifact, Decision, Evidence, Finding


def evidence() -> Evidence:
    return Evidence(
        id="evidence-1",
        source="p0-fixtures-v1",
        confidence=1,
        revision="semantic-seed-v1",
        evidence_uri="repo://bizguard/sample/coupon-service/redeem_service.py#RedeemService.redeem",
    )


def test_evidence_accepts_all_five_required_fields() -> None:
    assert evidence().revision == "semantic-seed-v1"


@pytest.mark.parametrize("missing", ["id", "source", "confidence", "revision", "evidence_uri"])
def test_evidence_rejects_each_missing_required_field(missing: str) -> None:
    payload = evidence().model_dump()
    del payload[missing]
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("confidence", 1.1), ("confidence", -0.1), ("evidence_uri", "bare-name")],
)
def test_evidence_rejects_invalid_contract_values(field: str, value: object) -> None:
    payload = evidence().model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


def test_change_context_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ChangeContext.model_validate(
            {
                "id": "context-1",
                "base_revision": "semantic-seed-v1",
                "diff_uri": "repo://bizguard/sample/diff.diff",
                "unexpected": True,
            }
        )


def test_change_context_accepts_caller_and_principal() -> None:
    context = ChangeContext(
        id="context-1",
        base_revision="semantic-seed-v1",
        diff_uri="repo://bizguard/sample/diff.diff",
        caller="prepare-change",
        principal="merchant_checkout",
    )
    assert context.principal == "merchant_checkout"


def test_changed_artifact_accepts_versioned_source_evidence() -> None:
    artifact = ChangedArtifact(
        id="artifact-1",
        uri="api://coupon-core/POST/v1/coupons/redeem#redeemCoupon",
        status=ArtifactStatus.MODIFIED,
        revision="semantic-seed-v1",
        evidence=[evidence()],
    )
    assert artifact.status is ArtifactStatus.MODIFIED


def test_decision_requires_a_nonempty_rationale() -> None:
    with pytest.raises(ValidationError):
        Decision(outcome=DecisionState.ALLOW, rationale="")


def test_finding_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        Finding(id="finding-1", status=CheckStatus.PASSED, message="ok", evidence=[])


def test_artifact_and_check_status_cannot_be_mixed() -> None:
    with pytest.raises(ValidationError):
        ChangedArtifact.model_validate(
            {
                "id": "artifact-1",
                "uri": "repo://bizguard/sample/coupon-service/redeem_service.py",
                "status": CheckStatus.PASSED,
                "revision": "semantic-seed-v1",
            }
        )
    with pytest.raises(ValidationError):
        Finding.model_validate(
            {
                "id": "finding-1",
                "status": ArtifactStatus.RENAMED,
                "message": "wrong enum",
                "evidence": [evidence().model_dump()],
            }
        )
