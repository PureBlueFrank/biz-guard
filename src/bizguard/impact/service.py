"""Shared Impact API used by CLI, Context Compiler, and MCP."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from bizguard.graph.indexer import index
from bizguard.impact.analyzer import analyze
from bizguard.semantic.models import CatalogRequiredTest, SemanticCatalog, load_catalog
from bizguard.semantic.required_tests import select_required_tests


class ImpactReport(BaseModel):
    changed_symbol: str
    revision: str
    layers: dict[str, list[str]]
    path: list[str]
    unknown_boundary: bool
    unknown_reason: str | None = None
    evidence: list[dict[str, object]]
    required_tests: list[dict[str, object]]
    required_approvers: list[str]


class ImpactService:
    def __init__(self, repositories_root: Path, catalog: SemanticCatalog | None = None) -> None:
        self._root = repositories_root
        self._catalog = catalog or load_catalog(Path(__file__).parents[1] / "semantic" / "catalog.yaml")

    def analyze(self, changed_symbol: str, revision: str, capability: str = "coupon_redemption") -> ImpactReport:
        snapshot = index(self._root, revision)
        result = analyze(snapshot, changed_symbol, revision)
        policies = [item for item in self._catalog.policies if item.capability == capability]
        tests: list[CatalogRequiredTest] = []
        for policy in policies:
            tests.extend(select_required_tests(self._catalog, capability, policy.id))
        owner = self._catalog.capability(capability).owner
        return ImpactReport(
            changed_symbol=changed_symbol,
            revision=revision,
            layers=result.layers,
            path=result.path,
            unknown_boundary=result.unknown_boundary,
            unknown_reason=result.unknown_reason,
            evidence=[item.model_dump(mode="json") for item in result.evidence],
            required_tests=[item.model_dump() for item in tests],
            required_approvers=[owner] if result.unknown_boundary else [],
        )
