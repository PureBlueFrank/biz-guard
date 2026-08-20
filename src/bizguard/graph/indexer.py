"""Build graph facts from source parsers and explicit fixture annotations."""

from __future__ import annotations

from pathlib import Path
import re

import yaml  # type: ignore[import-untyped]

from bizguard.analyzers.java_spring import JavaFact, analyze
from bizguard.analyzers.openapi_proto import analyze_openapi, analyze_proto
from bizguard.graph.ids import db_id
from bizguard.graph.models import EdgeKind, GraphEdge, GraphNode, GraphSnapshot, NodeKind


def index(repos: Path, revision: str) -> GraphSnapshot:
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    facts: list[tuple[str, Path, JavaFact]] = []

    def node(identifier: str, kind: NodeKind, label: str, **properties: str) -> None:
        existing = nodes.get(identifier)
        if existing is None:
            nodes[identifier] = GraphNode(identifier, kind, label, revision, properties)
        elif properties:
            nodes[identifier] = GraphNode(
                existing.id, existing.kind, existing.label, existing.revision,
                existing.properties | properties,
            )

    def edge(a: str, b: str, kind: EdgeKind, uri: str, source: str = "AST") -> None:
        candidate = GraphEdge(a, b, kind, source, 1.0 if source == "manual" else 0.9, revision, uri)
        if candidate.id not in {item.id for item in edges}:
            edges.append(candidate)

    for repo_dir in sorted(item for item in repos.iterdir() if item.is_dir()):
        repo = repo_dir.name
        repository_id = f"repo://{repo}"
        service_id = f"service://{repo}"
        node(repository_id, NodeKind.ORGANIZATION, repo)
        node(service_id, NodeKind.DEPLOYMENT, repo)
        edge(repository_id, service_id, EdgeKind.DEPLOYED_WITH, f"repo://{repo}/pom.xml")
        for path in repo_dir.rglob("*.java"):
            for fact in analyze(path, repo, revision):
                facts.append((repo, path, fact))
                if fact.kind == "parameter":
                    continue
                kind = NodeKind.INTERFACE if fact.kind == "field" else NodeKind.CODE
                node(fact.node_id, kind, fact.name)
                if fact.kind != "call":
                    edge(repository_id, fact.node_id, EdgeKind.DECLARES, fact.evidence_uri)
        for contract in repo_dir.rglob("*.yaml"):
            for contract_fact in analyze_openapi(contract, repo, revision):
                node(contract_fact.node_id, NodeKind.INTERFACE, contract_fact.name)
                edge(service_id, contract_fact.node_id, EdgeKind.EXPOSES, contract_fact.evidence_uri, "IDL")
        for contract in repo_dir.rglob("*.proto"):
            for contract_fact in analyze_proto(contract, revision, repository=repo):
                node(contract_fact.node_id, NodeKind.INTERFACE, contract_fact.name)
                edge(
                    service_id, contract_fact.node_id, EdgeKind.SERIALIZES_TO,
                    contract_fact.evidence_uri, "IDL",
                )

    _add_persistence_edges(facts, node, edge)
    _add_call_edges(facts, edge)
    _add_manual_edges(repos / "bizguard-manual-edges.yaml", nodes, node, edge)
    _add_business_nodes(nodes, node, edge)
    return GraphSnapshot(revision, _metadata(), list(nodes.values()), edges)


def _add_persistence_edges(
    facts: list[tuple[str, Path, JavaFact]],
    node: object,
    edge: object,
) -> None:
    entities = {(repo, fact.name) for repo, _, fact in facts if fact.kind == "class" and "Entity" in fact.annotations}
    for repo, _, fact in facts:
        if fact.kind != "field" or (repo, fact.owner) not in entities:
            continue
        table = _snake_case(fact.owner or "entity")
        column = _snake_case(fact.name)
        target = db_id(repo, table, column)
        node(target, NodeKind.DATA, f"{table}.{column}")  # type: ignore[operator]
        edge(fact.node_id, target, EdgeKind.MAPS_TO, fact.evidence_uri)  # type: ignore[operator]
        edge(target, f"service://{repo}", EdgeKind.DEPLOYED_WITH, fact.evidence_uri)  # type: ignore[operator]


def _add_call_edges(facts: list[tuple[str, Path, JavaFact]], edge: object) -> None:
    field_types = {
        (repo, fact.owner, fact.name): fact.type_name
        for repo, _, fact in facts
        if fact.kind == "field" and fact.owner and fact.type_name
    }
    methods = {
        (repo, fact.owner, fact.name): fact.node_id
        for repo, _, fact in facts
        if fact.kind == "method" and fact.owner
    }
    for repo, _, fact in facts:
        if fact.kind != "call" or not fact.owner or not fact.container or not fact.receiver:
            continue
        source = methods.get((repo, fact.owner, fact.container))
        target_owner = field_types.get((repo, fact.owner, fact.receiver))
        target = methods.get((repo, target_owner, fact.name)) if target_owner else None
        if source and target:
            edge(source, target, EdgeKind.CALLS, fact.evidence_uri)  # type: ignore[operator]


def _add_manual_edges(
    path: Path, nodes: dict[str, GraphNode], node: object, edge: object
) -> None:
    if not path.is_file():
        return
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for item in raw.get("edges", []):
        source_id = str(item["source_id"])
        target_id = str(item["target_id"])
        node(source_id, NodeKind.CODE, source_id.rsplit("#", 1)[-1])  # type: ignore[operator]
        node(target_id, _kind_for_id(target_id), target_id.rsplit("/", 1)[-1])  # type: ignore[operator]
        edge(source_id, target_id, EdgeKind(str(item["kind"])), str(item["evidence_uri"]), str(item["source"]))  # type: ignore[operator]
    for item in raw.get("boundary_nodes", []):
        identifier = str(item["id"])
        current = nodes.get(identifier)
        if current is None:
            node(  # type: ignore[operator]
                identifier, NodeKind.CODE, identifier.rsplit("#", 1)[-1], dynamic="true",
                boundary_evidence_uri=str(item["evidence_uri"]),
            )
        else:
            nodes[identifier] = GraphNode(
                current.id, current.kind, current.label, current.revision,
                current.properties | {"dynamic": "true", "boundary_evidence_uri": str(item["evidence_uri"])},
            )


def _add_business_nodes(nodes: dict[str, GraphNode], node: object, edge: object) -> None:
    capability = "capability://coupon-redemption"
    owner = "owner://coupon-platform"
    invariant = "invariant://redeem-idempotency-key-required"
    node(capability, NodeKind.BUSINESS, "coupon redemption")  # type: ignore[operator]
    node(owner, NodeKind.ORGANIZATION, "Coupon Platform")  # type: ignore[operator]
    node(invariant, NodeKind.BUSINESS, "idempotency key required")  # type: ignore[operator]
    for identifier, graph_node in list(nodes.items()):
        if graph_node.kind == NodeKind.DEPLOYMENT:
            edge(identifier, capability, EdgeKind.BELONGS_TO_CAPABILITY, "catalog://semantic/catalog.yaml#coupon_redemption", "catalog")  # type: ignore[operator]
    edge(capability, owner, EdgeKind.OWNED_BY, "catalog://semantic/catalog.yaml#coupon_redemption", "catalog")  # type: ignore[operator]
    edge(invariant, capability, EdgeKind.BELONGS_TO_CAPABILITY, "catalog://semantic/catalog.yaml#redeem_idempotency_key_required", "catalog")  # type: ignore[operator]


def _kind_for_id(identifier: str) -> NodeKind:
    if identifier.startswith("mq://"):
        return NodeKind.MESSAGING
    if identifier.startswith("db://"):
        return NodeKind.DATA
    if identifier.startswith(("api://", "proto://")):
        return NodeKind.INTERFACE
    return NodeKind.CODE


def _snake_case(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _metadata() -> dict[str, str]:
    return {"analyzer": "tree-sitter-java", "grammar": "0.23.5", "tree_sitter": "0.24.0", "jvm": "fixture-jdk17"}
