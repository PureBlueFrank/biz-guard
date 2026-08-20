"""Read-only symbol explanations derived from the indexed graph."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from bizguard.graph.indexer import index


class SymbolExplanation(BaseModel):
    symbol: str
    label: str
    kind: str
    revision: str
    evidence_uris: list[str]


class SymbolService:
    def __init__(self, repositories_root: Path) -> None:
        self._root = repositories_root

    def explain(self, symbol: str, revision: str) -> SymbolExplanation:
        snapshot = index(self._root, revision)
        node = next((item for item in snapshot.nodes if item.id == symbol), None)
        if node is None:
            raise ValueError(f"symbol is not indexed: {symbol}")
        evidence = sorted(
            {edge.evidence_uri for edge in snapshot.edges if symbol in {edge.source_id, edge.target_id}}
        )
        return SymbolExplanation(
            symbol=symbol, label=node.label, kind=node.kind.value, revision=revision, evidence_uris=evidence
        )
