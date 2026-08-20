from pathlib import Path

import pytest

from bizguard.semantic.models import load_catalog
from bizguard.semantic.models import SemanticCatalog
from bizguard.semantic.required_tests import select_required_tests


@pytest.fixture()
def catalog() -> SemanticCatalog:
    return load_catalog(Path(__file__).parents[1] / "src/bizguard/semantic/catalog.yaml")


@pytest.mark.parametrize(
    ("capability", "policy", "required", "bait"),
    [
        (
            "dto_field_contract",
            "coupon-dto-field-compatibility",
            "coupon-dto-contract-test",
            "coupon-ledger-audit-test",
        ),
        (
            "redemption_ledger",
            "coupon-ledger-auditability",
            "coupon-ledger-audit-test",
            "coupon-state-enum-test",
        ),
        (
            "redemption_state_machine",
            "coupon-state-transition-validity",
            "coupon-state-enum-test",
            "coupon-idempotency-test",
        ),
        (
            "redemption_idempotency",
            "coupon-redemption-idempotency-key",
            "coupon-idempotency-test",
            "coupon-public-api-test",
        ),
        (
            "redeem_public_api",
            "coupon-public-api-compatibility",
            "coupon-public-api-test",
            "coupon-private-helper-test",
        ),
        (
            "private_repository_helper",
            "coupon-private-helper-scope",
            "coupon-private-helper-test",
            "coupon-dto-contract-test",
        ),
    ],
)
def test_selects_only_matching_test_from_multiple_candidates(
    catalog: SemanticCatalog, capability: str, policy: str, required: str, bait: str
) -> None:
    ids = [item.id for item in select_required_tests(catalog, capability, policy)]
    assert ids == [required]
    assert bait not in ids
