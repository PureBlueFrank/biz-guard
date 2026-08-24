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
    "L5": {EdgeKind.BELONGS_TO_CAPABILITY, EdgeKind.PROTECTED_BY, EdgeKind.OWNED_BY},
}
_TRAVERSABLE = set().union(*_LAYER_KINDS.values())
_SEMANTIC_CONTINUATIONS = _LAYER_KINDS["L2"] | _LAYER_KINDS["L3"]


@dataclass(frozen=True)
class ImpactResult:
    """The layered path and evidence produced by impact analysis."""

    layers: dict[str, list[str]]
    path: list[str]
    paths: list[list[str]]
    evidence: list[Evidence]
    unknown_boundary: bool = False
    unknown_reason: str | None = None


def analyze(snapshot: GraphSnapshot, changed_id: str, revision: str) -> ImpactResult:
    """Find the shortest real graph route from a changed node to a business terminal."""
    if snapshot.revision != revision or changed_id not in {node.id for node in snapshot.nodes}:
        return _unknown_result(snapshot, changed_id, "NO_INDEXED_ROUTE")

    terminal_routes = _shortest_terminal_paths(snapshot, changed_id)
    if terminal_routes:
        paths = [_path(changed_id, edges) for edges in terminal_routes]
        primary_edges = terminal_routes[0]
        primary_path = paths[0]
        evidence = _evidence(
            [edge for route in terminal_routes for edge in route]
        )
        return ImpactResult(
            _layers(primary_path, primary_edges), primary_path, paths, _deduplicate(evidence)
        )

    node = next(item for item in snapshot.nodes if item.id == changed_id)
    reason = "DYNAMIC_BOUNDARY" if node.properties.get("dynamic") == "true" else "NO_INDEXED_ROUTE"
    return _unknown_result(snapshot, changed_id, reason, node.properties.get("boundary_evidence_uri"))


def _shortest_terminal_paths(snapshot: GraphSnapshot, start: str) -> list[list[GraphEdge]]:
    """Return one deterministic shortest path for every reachable business terminal."""
    adjacency: dict[str, list[GraphEdge]] = {}
    for edge in snapshot.edges:
        if edge.kind in _TRAVERSABLE:
            adjacency.setdefault(edge.source_id, []).append(edge)
            adjacency.setdefault(edge.target_id, []).append(edge)
    queue: deque[tuple[str, list[GraphEdge], bool]] = deque([(start, [], False)])
    seen = {(start, False)}
    routes: dict[str, list[GraphEdge]] = {}
    while queue:
        node, route, crossed_boundary = queue.popleft()
        if crossed_boundary and node != start and node.startswith(_TERMINAL_PREFIXES):
            routes.setdefault(node, route)
        for edge in sorted(adjacency.get(node, []), key=lambda item: item.id):
            if edge.kind in {
                EdgeKind.DECLARES,
                EdgeKind.DEPLOYED_WITH,
                EdgeKind.BELONGS_TO_CAPABILITY,
                EdgeKind.PROTECTED_BY,
                EdgeKind.OWNED_BY,
            } and not crossed_boundary:
                continue
            nxt = _other_end(edge, node)
            if nxt == start:
                continue
            next_crossed_boundary = crossed_boundary or edge.kind in _SEMANTIC_CONTINUATIONS
            state = (nxt, next_crossed_boundary)
            if state not in seen:
                seen.add(state)
                queue.append((nxt, [*route, edge], next_crossed_boundary))
    return [routes[key] for key in sorted(routes)]


def _path(start: str, edges: list[GraphEdge]) -> list[str]:
    result = [start]
    for edge in edges:
        result.append(_other_end(edge, result[-1]))
    return result


def _other_end(edge: GraphEdge, node: str) -> str:
    return edge.target_id if edge.source_id == node else edge.source_id


def _unknown_result(
    snapshot: GraphSnapshot, changed_id: str, reason: str, evidence_uri: str | None = None
) -> ImpactResult:
    uri = evidence_uri or f"graph://{snapshot.revision}/nodes/{changed_id}"
    source = "manual" if evidence_uri else "analysis"
    evidence = [
        Evidence(
            id=f"boundary:{changed_id}", source=source, confidence=1.0,
            revision=snapshot.revision, evidence_uri=uri,
        )
    ]
    return ImpactResult(
        _empty_layers(changed_id),
        [changed_id, "UNKNOWN_BOUNDARY"],
        [[changed_id, "UNKNOWN_BOUNDARY"]],
        evidence,
        True,
        reason,
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


def _deduplicate(items: list[Evidence]) -> list[Evidence]:
    return list({item.id: item for item in items}.values())
