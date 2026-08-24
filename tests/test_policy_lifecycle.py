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


def test_openapi_required_fields_must_have_matching_properties() -> None:
    full_content = """\
openapi: 3.0.0
components:
  schemas:
    Coupon:
      required: [redemptionId, status]
      properties:
        redemptionId: {type: string}
"""

    assert validate_artifact("published-dto-backward-compatible", full_content, "openapi.yaml")["violated"] is True


def test_proto_baseline_detects_removed_field_and_changed_number() -> None:
    before = "message Coupon { string id = 1; string status = 2; }"
    removed = "message Coupon { string id = 1; }"
    renumbered = "message Coupon { string id = 1; string status = 3; }"
    assert validate_artifact(
        "published-dto-backward-compatible",
        removed,
        "coupon.proto",
        baseline_source=before,
    )["violated"] is True
    assert validate_artifact(
        "published-dto-backward-compatible",
        renumbered,
        "coupon.proto",
        baseline_source=before,
    )["violated"] is True


def test_proto_baseline_detects_outer_field_after_nested_message() -> None:
    before = "message Outer { message Inner { string status = 1; } string id = 1; }"
    after = "message Outer { message Inner { string status = 1; } }"

    assert validate_artifact(
        "published-dto-backward-compatible",
        after,
        "coupon.proto",
        baseline_source=before,
    )["violated"] is True


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            "components:\n  schemas:\n    Coupon:\n      allOf:\n"
            "        - properties:\n            status: {type: string}\n",
            "components:\n  schemas:\n    Coupon:\n      allOf:\n"
            "        - properties: {}\n",
        ),
        (
            "swagger: '2.0'\ndefinitions:\n  Coupon:\n    properties:\n"
            "      status: {type: string}\n",
            "swagger: '2.0'\ndefinitions:\n  Coupon:\n    properties: {}\n",
        ),
        (
            "openapi: 3.0.0\ncomponents:\n  schemas:\n    CouponList:\n"
            "      type: array\n      items:\n        properties:\n"
            "          status: {type: string}\n",
            "openapi: 3.0.0\ncomponents:\n  schemas:\n    CouponList:\n"
            "      type: array\n      items:\n        properties: {}\n",
        ),
        (
            "openapi: 3.0.0\npaths:\n  /coupon:\n    get:\n      responses:\n"
            "        '200':\n          content:\n            application/json:\n"
            "              schema:\n                oneOf:\n                  - properties:\n"
            "                      status: {type: string}\n",
            "openapi: 3.0.0\npaths:\n  /coupon:\n    get:\n      responses:\n"
            "        '200':\n          content:\n            application/json:\n"
            "              schema:\n                oneOf:\n                  - properties: {}\n",
        ),
    ],
)
def test_openapi_composed_and_inline_schema_field_removal_is_detected(
    before: str, after: str
) -> None:
    assert validate_artifact(
        "published-dto-backward-compatible",
        after,
        "openapi.yaml",
        baseline_source=before,
    )["violated"] is True


def test_sql_start_transaction_is_accepted() -> None:
    result = validate_artifact(
        "redeem-ledger-consistency",
        "START TRANSACTION; UPDATE ledger SET status='SUCCESS'; COMMIT;",
        "migration.sql",
    )
    assert result["violated"] is False


def test_sql_write_after_cte_still_requires_a_transaction() -> None:
    result = validate_artifact(
        "redeem-ledger-consistency",
        "WITH rows AS (SELECT 1) UPDATE ledger SET status='SUCCESS';",
        "migration.sql",
    )
    assert result["violated"] is True


def test_sql_keywords_inside_strings_and_comments_are_ignored() -> None:
    result = validate_artifact(
        "redeem-ledger-consistency",
        "SELECT 'UPDATE ledger'; -- DELETE FROM ledger\nSELECT 1;",
        "migration.sql",
    )
    assert result["violated"] is False
