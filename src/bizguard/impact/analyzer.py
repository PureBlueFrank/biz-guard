"""Evidence-preserving shortest-path impact analysis over a graph snapshot."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from bizguard.domain.models import Evidence
from bizguard.graph.models import EdgeKind, GraphEdge, GraphSnapshot


_TERMINAL_PREFIXES = ("capability://", "invariant://", "owner://")
_LAYER_KINDS = {
    "L1": {EdgeKind.DECLARES},
    "L2": {EdgeKind.EXPOSES, EdgeKind.SERIALIZES_TO, EdgeKind.MAPS_TO},
    "L3": {EdgeKind.CONSUMES, EdgeKind.CALLS, EdgeKind.PUBLISHES, EdgeKind.OBSERVED_CALL},
    "L4": {EdgeKind.DEPLOYED_WITH},
    "L5": {EdgeKind.BELONGS_TO_CAPABILITY, EdgeKind.OWNED_BY},
}
_TRAVERSABLE = set().union(*_LAYER_KINDS.values())
_SEMANTIC_CONTINUATIONS = _LAYER_KINDS["L2"] | _LAYER_KINDS["L3"]


@dataclass(frozen=True)
class ImpactResult:
    layers: dict[str, list[str]]
    path: list[str]
    evidence: list[Evidence]
    unknown_boundary: bool = False


def analyze(snapshot: GraphSnapshot, changed_id: str, revision: str) -> ImpactResult:
    """Find the shortest real graph route from a changed node to a business terminal."""
    if snapshot.revision != revision or changed_id not in {node.id for node in snapshot.nodes}:
        return ImpactResult(_empty_layers(), [changed_id], [], False)

    path_edges = _shortest_terminal_path(snapshot, changed_id)
    if path_edges:
        path = [changed_id]
        for edge in path_edges:
            path.append(_other_end(edge, path[-1]))
        return ImpactResult(_layers(path, path_edges), path, _evidence(path_edges))

    node = next(item for item in snapshot.nodes if item.id == changed_id)
    if node.properties.get("dynamic") == "true" and not _has_semantic_continuation(snapshot, changed_id):
        evidence_uri = node.properties.get("boundary_evidence_uri")
        evidence = (
            [Evidence(
                id=f"boundary:{changed_id}", source="manual", confidence=1.0,
                revision=snapshot.revision, evidence_uri=evidence_uri,
            )]
            if evidence_uri
            else []
        )
        return ImpactResult(_empty_layers(changed_id), [changed_id, "UNKNOWN_BOUNDARY"], evidence, True)
    return ImpactResult(_empty_layers(changed_id), [changed_id], [], False)


def _shortest_terminal_path(snapshot: GraphSnapshot, start: str) -> list[GraphEdge]:
    adjacency: dict[str, list[GraphEdge]] = {}
    for edge in snapshot.edges:
        if edge.kind in _TRAVERSABLE:
            adjacency.setdefault(edge.source_id, []).append(edge)
            adjacency.setdefault(edge.target_id, []).append(edge)
    queue: deque[tuple[str, list[GraphEdge], bool]] = deque([(start, [], False)])
    seen = {(start, False)}
    while queue:
        node, route, crossed_boundary = queue.popleft()
        if crossed_boundary and node != start and node.startswith(_TERMINAL_PREFIXES):
            return route
        for edge in sorted(adjacency.get(node, []), key=lambda item: item.id):
            if edge.kind in {EdgeKind.DECLARES, EdgeKind.DEPLOYED_WITH, EdgeKind.BELONGS_TO_CAPABILITY, EdgeKind.OWNED_BY} and not crossed_boundary:
                continue
            nxt = _other_end(edge, node)
            if nxt == start:
                continue
            next_crossed_boundary = crossed_boundary or edge.kind in _SEMANTIC_CONTINUATIONS
            state = (nxt, next_crossed_boundary)
            if state not in seen:
                seen.add(state)
                queue.append((nxt, [*route, edge], next_crossed_boundary))
    return []


def _other_end(edge: GraphEdge, node: str) -> str:
    return edge.target_id if edge.source_id == node else edge.source_id


def _has_semantic_continuation(snapshot: GraphSnapshot, node_id: str) -> bool:
    return any(
        edge.kind in _SEMANTIC_CONTINUATIONS and node_id in {edge.source_id, edge.target_id}
        for edge in snapshot.edges
    )


def _layers(path: list[str], edges: list[GraphEdge]) -> dict[str, list[str]]:
    result = _empty_layers(path[0])
    for edge, target in zip(edges, path[1:]):
        for layer, kinds in _LAYER_KINDS.items():
            if edge.kind in kinds:
                result[layer].append(target)
                break
    return result


def _empty_layers(start: str | None = None) -> dict[str, list[str]]:
    return {"L1": [start] if start else [], "L2": [], "L3": [], "L4": [], "L5": []}


def _evidence(edges: list[GraphEdge]) -> list[Evidence]:
    return [
        Evidence(
            id=f"edge:{edge.id}", source=edge.source, confidence=edge.confidence,
            revision=edge.revision, evidence_uri=edge.evidence_uri,
        )
        for edge in edges
    ]
