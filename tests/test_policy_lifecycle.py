import pytest

from bizguard.policy.lifecycle import PolicyLifecycle, PolicyMode, PromotionGates
from bizguard.policy.validators import validate_artifact


@pytest.mark.parametrize(
    ("policy", "source", "path", "violated"),
    [
        ("redeem-ledger-consistency", "BEGIN; UPDATE ledger SET status='SUCCESS'; COMMIT;", "x.sql", False),
        ("redeem-ledger-consistency", "UPDATE ledger SET status='SUCCESS';", "x.sql", True),
        ("redeem-ledger-consistency", "-- only comments", "x.sql", False),
        ("published-dto-backward-compatible", "message A { string id = 1; }", "x.proto", False),
        ("published-dto-backward-compatible", "message A { string name = 1; }", "x.proto", True),
        ("published-dto-backward-compatible", "syntax = 'proto3';", "x.proto", False),
        ("coupon-write-consumes-idempotency-key", "schema_version: 1", "x.avsc", False),
        ("coupon-write-consumes-idempotency-key", "type: record", "x.avsc", True),
        ("coupon-write-consumes-idempotency-key", "version: 1", "x.avsc", False),
    ],
)
def test_frozen_policy_artifacts_are_content_validated(policy: str, source: str, path: str, violated: bool) -> None:
    assert validate_artifact(policy, source, path)["violated"] is violated


def test_lifecycle_promotes_and_rolls_back_from_fixture_gate() -> None:
    lifecycle = PolicyLifecycle(policy_id="p", samples=3)
    gates = PromotionGates(min_samples=3, max_false_positive_rate=0.0)
    for expected in (PolicyMode.SHADOW, PolicyMode.WARNING, PolicyMode.BLOCKING):
        lifecycle.promote(gates)
        assert lifecycle.mode is expected
    lifecycle.rollback()
    assert lifecycle.mode is PolicyMode.WARNING


def test_lifecycle_rejects_unmeasured_promotion() -> None:
    with pytest.raises(ValueError):
        PolicyLifecycle(policy_id="p").promote(PromotionGates(min_samples=1, max_false_positive_rate=0.0))


def test_proto_required_fields_are_checked_against_full_file_content() -> None:
    full_content = "syntax = 'proto3';\nmessage Coupon {\n  string name = 1;\n}\n"
    assert validate_artifact("published-dto-backward-compatible", full_content, "coupon.proto")["violated"] is True
