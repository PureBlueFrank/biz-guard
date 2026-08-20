"""Contract evidence collected from a caller-selected repository snapshot."""

from pathlib import Path

from bizguard.domain.models import Evidence
from bizguard.graph.indexer import index


class ContractProvider:
    """Collect interface-contract evidence from an indexed snapshot."""

    def __init__(self, repos: Path, revision: str) -> None:
        self.repos = repos
        self.revision = revision

    def collect(self) -> list[Evidence]:
        return [
            Evidence(id=f"edge:{edge.id}", source=edge.source, confidence=edge.confidence,
                     revision=edge.revision, evidence_uri=edge.evidence_uri)
            for edge in index(self.repos, self.revision).edges
            if edge.source == "IDL"
        ]
