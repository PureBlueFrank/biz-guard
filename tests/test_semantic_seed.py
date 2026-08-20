"""Catalog seed completeness and fixture alignment tests."""

from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from bizguard.graph.ids import db_id, java_symbol, repo_id


ROOT = Path(__file__).parent.parent
CATALOG_PATH = ROOT / "src" / "bizguard" / "semantic" / "catalog.yaml"


def _catalog() -> dict[str, Any]:
    loaded = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


def test_catalog_declares_frozen_schema_and_revision() -> None:
    catalog = _catalog()
    assert catalog["schema_version"] == 1
    assert catalog["revision"] == "semantic-seed-v1"


def test_catalog_contains_required_semantic_sections() -> None:
    catalog = _catalog()
    for section in ("capabilities", "owners", "entities", "states", "invariants", "policies", "required_tests"):
        assert catalog[section]


def test_coupon_redemption_seed_links_capability_owner_and_policy() -> None:
    catalog = _catalog()
    capability = next(item for item in catalog["capabilities"] if item["id"] == "coupon_redemption")
    policy = next(item for item in catalog["policies"] if item["id"] == "coupon-redemption-idempotency-key")
    assert capability["owner"] == policy["owner"] == "coupon_platform"
    assert policy["invariant"] == "redeem_idempotency_key_required"


def test_catalog_references_all_three_fixture_repositories() -> None:
    catalog = _catalog()
    repositories = set(next(item for item in catalog["capabilities"] if item["id"] == "coupon_redemption")["repositories"])
    for repository in repositories:
        assert (ROOT / "fixtures" / "java-microservices" / repository / "pom.xml").is_file()


def test_required_test_targets_coupon_core_offline_build() -> None:
    catalog = _catalog()
    required_test = next(item for item in catalog["required_tests"] if item["id"] == "coupon-core-redeem-idempotency-test")
    assert required_test["repository"] == "coupon-core"
    assert required_test["command"] == "./mvnw --offline test"


def test_catalog_has_manual_mapper_ledger_failed_state_and_a_second_test() -> None:
    catalog = _catalog()
    assert catalog["manual_mapper_edges"][0]["id"] == "coupon-redemption-dynamic-mapper"
    assert any(state["value"] == "FAILED" for state in catalog["states"])
    assert {"coupon-core", "merchant-service"}.issubset({item["repository"] for item in catalog["required_tests"]})


def test_catalog_canonical_references_regenerate_and_target_real_fixture_sources() -> None:
    mapper = _catalog()["manual_mapper_edges"][0]
    expected_source = repo_id(
        "coupon-core",
        "src/main/java/com/bizguard/coupon/persistence/DynamicCouponMapper.java",
        java_symbol("DynamicCouponMapper", "map", ("java.util.Map",)),
    )
    assert mapper["source_id"] == expected_source
    assert mapper["target_id"] == db_id("coupon-core", "coupon_redemption", "status")
    source_path = ROOT / "fixtures" / "java-microservices" / "coupon-core" / expected_source.split("#", 1)[0].split("/", 3)[3]
    assert source_path.is_file()
    assert "map(" in source_path.read_text(encoding="utf-8")

    invariant = next(item for item in _catalog()["invariants"] if item["id"] == "redeem_idempotency_key_required")
    expected_invariant_source = repo_id(
        "coupon-core",
        "src/main/java/com/bizguard/coupon/application/RedeemService.java",
        java_symbol("RedeemService", "redeem", ("CouponRequest",)),
    )
    assert invariant["source_id"] == expected_invariant_source
    invariant_path = ROOT / "fixtures" / "java-microservices" / "coupon-core" / expected_invariant_source.split("#", 1)[0].split("/", 3)[3]
    assert invariant_path.is_file()
    assert "redeem(" in invariant_path.read_text(encoding="utf-8")


def test_all_catalog_db_ids_belong_to_the_table_owner_repository() -> None:
    catalog = _catalog()
    for entity in catalog["entities"]:
        scheme, rest = entity["canonical_id"].split("://", 1)
        repository, table_and_column = rest.split("/", 1)
        table, _, column = table_and_column.partition("#")
        assert scheme == "db"
        assert repository == entity["repository"]
        assert entity["canonical_id"] == db_id(repository, table, column or None)
