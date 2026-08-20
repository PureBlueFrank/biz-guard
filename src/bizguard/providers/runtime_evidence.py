"""Runtime evidence collected from a caller-selected trace."""

from pathlib import Path

from bizguard.domain.models import Evidence
from bizguard.graph.indexer import index
from bizguard.graph.runtime import import_trace


class RuntimeEvidenceProvider:
    def __init__(self, repos: Path, trace: Path, revision: str) -> None:
        self.repos = repos
        self.trace = trace
        self.revision = revision

    def collect(self) -> list[Evidence]:
        snapshot = import_trace(index(self.repos, self.revision), self.trace)
        return [
            Evidence(id=f"edge:{edge.id}", source=edge.source, confidence=edge.confidence,
                     revision=edge.revision, evidence_uri=edge.evidence_uri)
            for edge in snapshot.edges
            if edge.source == "Trace"
        ]
