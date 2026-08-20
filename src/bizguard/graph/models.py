"""Versioned, evidence-carrying graph primitives."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, cast


class NodeKind(StrEnum):
    """Supported graph node categories."""

    ORGANIZATION = "organization"
    DEPLOYMENT = "deployment"
    CODE = "code"
    INTERFACE = "interface"
    DATA = "data"
    MESSAGING = "messaging"
    RUNTIME = "runtime"
    BUSINESS = "business"


class EdgeKind(StrEnum):
    """Supported relationships between graph nodes."""

    DECLARES = "DECLARES"
    CALLS = "CALLS"
    IMPLEMENTS = "IMPLEMENTS"
    EXTENDS = "EXTENDS"
    READS = "READS"
    WRITES = "WRITES"
    EXPOSES = "EXPOSES"
    INVOKES = "INVOKES"
    SERIALIZES_TO = "SERIALIZES_TO"
    DESERIALIZES_FROM = "DESERIALIZES_FROM"
    MAPS_TO = "MAPS_TO"
    PUBLISHES = "PUBLISHES"
    CONSUMES = "CONSUMES"
    DEPENDS_ON = "DEPENDS_ON"
    DEPLOYED_WITH = "DEPLOYED_WITH"
    BELONGS_TO_CAPABILITY = "BELONGS_TO_CAPABILITY"
    MANIPULATES_ENTITY = "MANIPULATES_ENTITY"
    PROTECTED_BY = "PROTECTED_BY"
    VIOLATES = "VIOLATES"
    OWNED_BY = "OWNED_BY"
    OBSERVED_CALL = "OBSERVED_CALL"


@dataclass(frozen=True)
class GraphNode:
    """A versioned node in the dependency graph."""

    id: str
    kind: NodeKind
    label: str
    revision: str
    properties: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    """An evidence-carrying relationship between graph nodes."""

    source_id: str
    target_id: str
    kind: EdgeKind
    source: str
    confidence: float
    revision: str
    evidence_uri: str
    first_seen: str | None = None
    last_seen: str | None = None

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.source_id}->{self.target_id}"


@dataclass
class GraphSnapshot:
    """A complete graph view for one source revision."""

    revision: str
    metadata: dict[str, str]
    content_digest: str = ""
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "metadata": self.metadata,
            "content_digest": self.content_digest,
            "nodes": [asdict(item) | {"kind": item.kind.value} for item in self.nodes],
            "edges": [asdict(item) | {"kind": item.kind.value} for item in self.edges],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "GraphSnapshot":
        metadata = cast(dict[str, str], raw.get("metadata", {}))
        raw_nodes = cast(list[dict[str, Any]], raw.get("nodes", []))
        raw_edges = cast(list[dict[str, Any]], raw.get("edges", []))
        return cls(
            str(raw["revision"]),
            metadata,
            str(raw.get("content_digest", "")),
            [GraphNode(**(item | {"kind": NodeKind(item["kind"])})) for item in raw_nodes],
            [GraphEdge(**(item | {"kind": EdgeKind(item["kind"])})) for item in raw_edges],
        )
