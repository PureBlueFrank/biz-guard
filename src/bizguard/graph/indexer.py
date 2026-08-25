"""Build graph facts from source parsers and explicit fixture annotations."""

from __future__ import annotations

from pathlib import Path
import re
from hashlib import sha256
from collections.abc import Mapping

import yaml  # type: ignore[import-untyped]

from bizguard.analyzers.java_spring import JavaFact, analyze
from bizguard.analyzers.openapi_proto import analyze_openapi, analyze_proto
from bizguard.graph.ids import db_id
from bizguard.graph.models import EdgeKind, GraphEdge, GraphNode, GraphSnapshot, NodeKind
from bizguard.semantic.models import SemanticCatalog, load_catalog


def index(
    repos: Path, revision: str, catalog: SemanticCatalog | None = None
) -> GraphSnapshot:
    """Build a graph snapshot from repository source and contract files."""
    catalog = catalog or load_catalog(Path(__file__).parents[1] / "semantic/catalog.yaml")
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
        for path in sorted(repo_dir.rglob("*.java")):
            for fact in analyze(path, repo, revision):
                facts.append((repo, path, fact))
                if fact.kind == "parameter":
                    continue
                if fact.kind == "call":
                    node(fact.node_id, NodeKind.CODE, fact.name)
                    continue
                kind = NodeKind.INTERFACE if fact.kind == "field" else NodeKind.CODE
                node(fact.node_id, kind, fact.name)
                edge(repository_id, fact.node_id, EdgeKind.DECLARES, fact.evidence_uri)
        for contract in sorted(repo_dir.rglob("*.yaml")):
            for contract_fact in analyze_openapi(contract, repo, revision):
                node(contract_fact.node_id, NodeKind.INTERFACE, contract_fact.name)
                edge(service_id, contract_fact.node_id, EdgeKind.EXPOSES, contract_fact.evidence_uri, "IDL")
        for contract in sorted(repo_dir.rglob("*.proto")):
            for contract_fact in analyze_proto(contract, revision, repository=repo):
                node(contract_fact.node_id, NodeKind.INTERFACE, contract_fact.name)
                edge(
                    service_id, contract_fact.node_id, EdgeKind.SERIALIZES_TO,
                    contract_fact.evidence_uri, "IDL",
                )

    _add_persistence_edges(facts, node, edge)
    _add_call_edges(facts, edge)
    _add_manual_edges(repos / "bizguard-manual-edges.yaml", nodes, node, edge)
    _add_business_nodes(nodes, node, edge, catalog)
    digest = content_digest(repos)
    return GraphSnapshot(
        revision,
        _metadata() | {"content_digest": digest},
        digest,
        [nodes[key] for key in sorted(nodes)],
        sorted(edges, key=lambda item: item.id),
    )


def content_digest(
    repos: Path,
    *,
    content_overrides: Mapping[str, bytes | None] | None = None,
) -> str:
    """Hash indexed inputs, optionally replacing paths with virtual diff content."""
    digest = sha256()
    repositories = sorted(item for item in repos.iterdir() if item.is_dir())
    for repository in repositories:
        name = repository.name.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
    indexed_files = {
        path.relative_to(repos).as_posix(): path.read_bytes()
        for repository in repositories
        for pattern in ("*.java", "*.yaml", "*.proto")
        for path in repository.rglob(pattern)
        if path.is_file()
    }
    manual_edges = repos / "bizguard-manual-edges.yaml"
    if manual_edges.is_file():
        indexed_files[manual_edges.relative_to(repos).as_posix()] = manual_edges.read_bytes()
    for relative, content in (content_overrides or {}).items():
        if content is None:
            indexed_files.pop(relative, None)
        else:
            indexed_files[relative] = content
    for path, content in sorted(indexed_files.items()):
        encoded_path = path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


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


def _add_business_nodes(
    nodes: dict[str, GraphNode],
    node: object,
    edge: object,
    catalog: SemanticCatalog,
) -> None:
    """Attach only capabilities explicitly selected by the organization catalog."""
    graph_capabilities = [capability for capability in catalog.capabilities if capability.graph]
    if not graph_capabilities:
        raise ValueError("semantic catalog must select at least one graph capability")
    owners = {owner.id: owner for owner in catalog.owners}
    for capability in graph_capabilities:
        capability_id = f"capability://{capability.id.replace('_', '-')}"
        owner_id = f"owner://{capability.owner.replace('_', '-')}"
        owner = owners.get(capability.owner)
        evidence = f"catalog://semantic/catalog.yaml#{capability.id}"
        node(capability_id, NodeKind.BUSINESS, capability.name)  # type: ignore[operator]
        node(  # type: ignore[operator]
            owner_id,
            NodeKind.ORGANIZATION,
            owner.name if owner is not None else capability.owner,
        )
        for repository in capability.repositories:
            deployment_id = f"service://{repository}"
            if deployment_id in nodes:
                edge(  # type: ignore[operator]
                    deployment_id,
                    capability_id,
                    EdgeKind.BELONGS_TO_CAPABILITY,
                    evidence,
                    "catalog",
                )
        edge(capability_id, owner_id, EdgeKind.OWNED_BY, evidence, "catalog")  # type: ignore[operator]
        for invariant in catalog.invariants:
            if invariant.capability != capability.id:
                continue
            invariant_id = f"invariant://{invariant.id.replace('_', '-')}"
            invariant_evidence = f"catalog://semantic/catalog.yaml#{invariant.id}"
            node(invariant_id, NodeKind.BUSINESS, invariant.statement)  # type: ignore[operator]
            edge(  # type: ignore[operator]
                invariant_id,
                capability_id,
                EdgeKind.BELONGS_TO_CAPABILITY,
                invariant_evidence,
                "catalog",
            )
            edge(  # type: ignore[operator]
                capability_id,
                invariant_id,
                EdgeKind.PROTECTED_BY,
                invariant_evidence,
                "catalog",
            )


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
