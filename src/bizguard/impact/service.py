"""Shared Impact API used by CLI, Context Compiler, and MCP."""

from __future__ import annotations

from pathlib import Path
import re
from threading import RLock

from pydantic import BaseModel

from bizguard.graph.indexer import content_digest, index
from bizguard.graph.models import GraphSnapshot
from bizguard.impact.analyzer import analyze
from bizguard.semantic.models import CatalogRequiredTest, SemanticCatalog, load_catalog
from bizguard.semantic.required_tests import select_required_tests


class ImpactReport(BaseModel):
    """The serializable impact-analysis response."""

    changed_symbol: str
    revision: str
    layers: dict[str, list[str]]
    path: list[str]
    paths: list[list[str]]
    unknown_boundary: bool
    unknown_reason: str | None = None
    evidence: list[dict[str, object]]
    required_tests: list[dict[str, object]]
    required_approvers: list[str]


class ImpactService:
    """Build impact reports from source graphs and semantic policy metadata."""

    def __init__(self, repositories_root: Path, catalog: SemanticCatalog | None = None) -> None:
        self._root = repositories_root
        self._catalog = catalog or load_catalog(Path(__file__).parents[1] / "semantic" / "catalog.yaml")
        self._snapshots: dict[str, GraphSnapshot] = {}
        self._snapshot_lock = RLock()

    def analyze(
        self,
        changed_symbol: str,
        revision: str,
        capability: str | None = None,
        diff_text: str | None = None,
        snapshot: GraphSnapshot | None = None,
    ) -> ImpactReport:
        if snapshot is None:
            digest = content_digest(self._root)
            with self._snapshot_lock:
                snapshot = self._snapshots.get(revision)
                if snapshot is None or snapshot.content_digest != digest:
                    snapshot = index(self._root, revision, self._catalog)
                    self._snapshots[revision] = snapshot
        result = analyze(snapshot, changed_symbol, revision)
        capability = capability or self._infer_capability(changed_symbol, diff_text)
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
            paths=result.paths,
            unknown_boundary=result.unknown_boundary,
            unknown_reason=result.unknown_reason,
            evidence=[item.model_dump(mode="json") for item in result.evidence],
            required_tests=[item.model_dump() for item in tests],
            required_approvers=[owner] if result.unknown_boundary else [],
        )

    def _infer_capability(self, changed_symbol: str, diff_text: str | None) -> str:
        """Select the catalog capability whose semantic facts best explain a diff."""
        signals = _diff_signals(changed_symbol, diff_text)
        candidates: list[tuple[int, str]] = []
        for capability in self._catalog.capabilities:
            policies = [policy for policy in self._catalog.policies if policy.capability == capability.id]
            tests = [test for test in self._catalog.required_tests if test.capability == capability.id]
            if not policies or not tests:
                continue
            invariants = [
                invariant for invariant in self._catalog.invariants if invariant.capability == capability.id
            ]
            source_terms = _terms(" ".join(item.source_id for item in invariants)) - _SOURCE_PATH_TERMS
            semantic_terms = _terms(
                " ".join(
                    [
                        capability.id,
                        capability.name,
                        *(policy.id for policy in policies),
                        *(invariant.id for invariant in invariants),
                        *(invariant.statement for invariant in invariants),
                    ]
                )
            )
            score = 6 * len(signals & source_terms) + 4 * len(signals & semantic_terms)
            score += 4 * len(signals & set(capability.repositories))
            candidates.append((score, capability.id))
        if not candidates:
            raise ValueError("semantic catalog has no capability with a policy and required test")
        best_score = max(score for score, _ in candidates)
        best = sorted(identifier for score, identifier in candidates if score == best_score)
        if best_score == 0 or len(best) != 1:
            raise ValueError(f"unable to infer a unique capability for changed symbol: {changed_symbol}")
        return best[0]


def _diff_signals(changed_symbol: str, diff_text: str | None) -> set[str]:
    """Extract catalog-comparable signals from a changed symbol and unified diff."""
    # A diff is authoritative: graph symbol matching can resolve a nearby indexed
    # field when the removed field itself is no longer present in the snapshot.
    text = diff_text if diff_text is not None else changed_symbol
    signals = _terms(text)
    if diff_text is None:
        return signals
    paths = re.findall(r"^--- a/(.+)$", diff_text, re.MULTILINE)
    if any(path.endswith(".proto") for path in paths):
        signals.update({"contract", "dto"})
    if re.search(r"^[-+]\s*\w+\s+\w+\s*=\s*\d+\s*;", diff_text, re.MULTILINE):
        signals.add("field")
    return signals


def _terms(value: str) -> set[str]:
    return {
        term.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", value)
        for term in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", token.replace("_", " "))
        if len(term) > 1
    }


_SOURCE_PATH_TERMS = {"bizguard", "com", "java", "main", "src"}
