from pathlib import Path

import pytest

from bizguard.semantic.models import load_catalog
from bizguard.semantic.models import SemanticCatalog


@pytest.fixture()
def catalog() -> SemanticCatalog:
    return load_catalog(Path(__file__).parents[1] / "src/bizguard/semantic/catalog.yaml")


@pytest.mark.parametrize(
    "identifier", ["coupon_redemption", "coupon_redemption", "coupon_redemption"]
)
def test_capability_is_frozen(catalog: SemanticCatalog, identifier: str) -> None:
    assert catalog.capability(identifier).owner == "coupon_platform"


@pytest.mark.parametrize("identifier", ["coupon_platform", "merchant_checkout"])
def test_owner_mapping(catalog: SemanticCatalog, identifier: str) -> None:
    assert catalog.owner(identifier).repositories
