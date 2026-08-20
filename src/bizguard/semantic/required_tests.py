"""Minimal required-test selection, deliberately excluding unrelated test bait."""

from __future__ import annotations

from bizguard.semantic.models import CatalogRequiredTest, SemanticCatalog


def select_required_tests(catalog: SemanticCatalog, capability: str, policy_id: str) -> list[CatalogRequiredTest]:
    owner = catalog.capability(capability).owner
    return [
        test
        for test in catalog.required_tests
        if test.capability == capability and test.policy == policy_id and test.owner == owner
    ]
