from pathlib import Path

import pytest

from bizguard.semantic.models import load_catalog
from bizguard.semantic.models import SemanticCatalog
from bizguard.semantic.required_tests import select_required_tests


@pytest.fixture()
def catalog() -> SemanticCatalog:
    return load_catalog(Path(__file__).parents[1] / "src/bizguard/semantic/catalog.yaml")


@pytest.mark.parametrize("label", ["dto", "ledger", "state", "key", "api"])
def test_selects_only_owner_test_not_bait(catalog: SemanticCatalog, label: str) -> None:
    ids = [item.id for item in select_required_tests(catalog, "coupon_redemption", "coupon-redemption-idempotency-key")]
    assert ids == ["coupon-core-redeem-idempotency-test"]
    assert "merchant-service-coupon-status-test" not in ids
