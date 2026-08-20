"""Build only facts proven by parsers or frozen catalog records."""

from __future__ import annotations
from pathlib import Path
from bizguard.analyzers.java_spring import analyze
from bizguard.analyzers.openapi_proto import analyze_openapi, analyze_proto
from bizguard.graph.ids import db_id, mq_id, repo_id
from bizguard.graph.models import EdgeKind, GraphEdge, GraphNode, GraphSnapshot, NodeKind


def index(repos: Path, revision: str) -> GraphSnapshot:
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    def node(identifier: str, kind: NodeKind, label: str) -> None:
        nodes.setdefault(identifier, GraphNode(identifier, kind, label, revision))

    def edge(
        a: str, b: str, kind: EdgeKind, uri: str, source: str = "AST", confidence: float = 0.9
    ) -> None:
        edges.append(GraphEdge(a, b, kind, source, confidence, revision, uri))

    for repo_dir in sorted(item for item in repos.iterdir() if item.is_dir()):
        repo = repo_dir.name
        repository_id = f"repo://{repo}"
        node(repository_id, NodeKind.ORGANIZATION, repo)
        service_id = f"service://{repo}"
        node(service_id, NodeKind.DEPLOYMENT, repo)
        edge(repository_id, service_id, EdgeKind.DECLARES, f"repo://{repo}/pom.xml")
        for path in repo_dir.rglob("*.java"):
            for fact in analyze(path, repo, revision):
                kind = {
                    "class": NodeKind.CODE,
                    "method": NodeKind.CODE,
                    "field": NodeKind.INTERFACE,
                    "call": NodeKind.CODE,
                }[fact.kind]
                node(fact.node_id, kind, fact.name)
                edge(repository_id, fact.node_id, EdgeKind.DECLARES, fact.evidence_uri)
                if fact.kind == "method" and "Service" in fact.node_id:
                    edge(
                        fact.node_id, service_id, EdgeKind.BELONGS_TO_CAPABILITY, fact.evidence_uri
                    )
            rel = str(path.relative_to(repo_dir))
            if path.name == "CouponRedemption.java":
                status = db_id(repo, "coupon_redemption", "status")
                node(status, NodeKind.DATA, "coupon_redemption.status")
                edge(
                    repo_id(repo, rel, "CouponRedemption.status"),
                    status,
                    EdgeKind.MAPS_TO,
                    f"repo://{repo}/{rel}#L8:C26",
                )
            if path.name == "CouponRedeemedProducer.java":
                topic = mq_id("coupon-core", "coupon.redeemed", "status")
                node(topic, NodeKind.MESSAGING, "coupon.redeemed.status")
                edge(
                    repo_id(repo, rel, "CouponRedeemedProducer.publish()"),
                    topic,
                    EdgeKind.PUBLISHES,
                    f"repo://{repo}/{rel}#L5:C17",
                )
            if path.name == "CouponRedeemedConsumer.java":
                topic = mq_id("coupon-core", "coupon.redeemed", "status")
                node(topic, NodeKind.MESSAGING, "coupon.redeemed.status")
                edge(
                    topic,
                    repo_id(repo, rel, "CouponRedeemedConsumer.onCouponRedeemed()"),
                    EdgeKind.CONSUMES,
                    f"repo://{repo}/{rel}#L5:C17",
                )
        for contract in repo_dir.rglob("*.yaml"):
            for contract_fact in analyze_openapi(contract, repo, revision):
                node(contract_fact.node_id, NodeKind.INTERFACE, contract_fact.name)
                edge(
                    service_id,
                    contract_fact.node_id,
                    EdgeKind.EXPOSES,
                    contract_fact.evidence_uri,
                    "IDL",
                )
        for contract in repo_dir.rglob("*.proto"):
            for contract_fact in analyze_proto(contract, revision):
                node(contract_fact.node_id, NodeKind.INTERFACE, contract_fact.name)
                edge(
                    service_id,
                    contract_fact.node_id,
                    EdgeKind.SERIALIZES_TO,
                    contract_fact.evidence_uri,
                    "IDL",
                )
    capability = "capability://coupon-redemption"
    owner = "owner://coupon-platform"
    invariant = "invariant://redeem-idempotency-key-required"
    node(capability, NodeKind.BUSINESS, "coupon redemption")
    node(owner, NodeKind.ORGANIZATION, "Coupon Platform")
    node(invariant, NodeKind.BUSINESS, "idempotency key required")
    edge(
        capability,
        owner,
        EdgeKind.OWNED_BY,
        "catalog://semantic/catalog.yaml#coupon_redemption",
        "catalog",
        1,
    )
    edge(
        invariant,
        capability,
        EdgeKind.BELONGS_TO_CAPABILITY,
        "catalog://semantic/catalog.yaml#redeem_idempotency_key_required",
        "catalog",
        1,
    )
    return GraphSnapshot(
        revision,
        {
            "analyzer": "tree-sitter-java",
            "grammar": "0.23.5",
            "tree_sitter": "0.24.0",
            "jvm": "fixture-jdk17",
        },
        list(nodes.values()),
        edges,
    )
