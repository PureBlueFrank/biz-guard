from pathlib import Path

from bizguard.eval.impact import _independent_shortest_path
from bizguard.graph.indexer import index
from bizguard.graph.models import EdgeKind, GraphEdge, GraphNode, GraphSnapshot, NodeKind
from bizguard.impact.analyzer import analyze


ROOT = Path("fixtures/java-microservices")


def test_analysis_uses_changed_node_and_real_shortest_path() -> None:
    snapshot = index(ROOT, "r")
    dto = "repo://coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java#CouponResponse.status"
    mapper = "repo://coupon-core/src/main/java/com/bizguard/coupon/persistence/DynamicCouponMapper.java#DynamicCouponMapper.map()"
    assert analyze(snapshot, dto, "r").path != analyze(snapshot, mapper, "r").path
    assert analyze(snapshot, dto, "r").path[0] == dto


def test_dynamic_boundary_is_graph_property_not_name_match() -> None:
    snapshot = index(ROOT, "r")
    mapper = "repo://coupon-core/src/main/java/com/bizguard/coupon/persistence/DynamicCouponMapper.java#DynamicCouponMapper.map()"
    assert analyze(snapshot, mapper, "r").unknown_boundary
    assert analyze(snapshot, "repo://coupon-core/DynamicCouponMapperWhatever", "r").unknown_reason == "NO_INDEXED_ROUTE"


def test_path_evidence_is_taken_from_traversed_edges() -> None:
    snapshot = index(ROOT, "r")
    changed = "repo://coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java#CouponResponse.status"
    result = analyze(snapshot, changed, "r")
    edge_uris = {edge.evidence_uri for edge in snapshot.edges}
    assert result.evidence
    assert all(item.evidence_uri in edge_uris for item in result.evidence)


def test_unindexed_route_is_explicitly_unknown() -> None:
    snapshot = index(ROOT, "r")
    result = analyze(snapshot, "unindexed-route://coupon-core/v2/coupons/redeem", "r")
    assert result.path == ["unindexed-route://coupon-core/v2/coupons/redeem", "UNKNOWN_BOUNDARY"]
    assert result.unknown_boundary
    assert result.unknown_reason == "NO_INDEXED_ROUTE"


def test_isolated_synthetic_call_is_explicitly_unknown() -> None:
    call = "repo://coupon-core/Example.java#call%3Aunresolved%401"
    snapshot = GraphSnapshot(
        "r", {}, "", [GraphNode(call, NodeKind.CODE, "unresolved", "r")], []
    )
    result = analyze(snapshot, call, "r")
    assert result.path == [call, "UNKNOWN_BOUNDARY"]
    assert result.unknown_reason == "NO_INDEXED_ROUTE"


def test_capability_protects_invariant_with_a_reachable_edge() -> None:
    snapshot = index(ROOT, "r")
    assert any(
        edge.source_id == "capability://coupon-redemption"
        and edge.target_id == "invariant://redeem-idempotency-key-required"
        and edge.kind == EdgeKind.PROTECTED_BY
        for edge in snapshot.edges
    )


def test_dynamic_dead_end_has_the_same_unknown_fallback_in_evaluator() -> None:
    dynamic = "repo://coupon-core/Dynamic.java#Dynamic.map()"
    dead_end = "api://coupon-contract/SCHEMA/dead-end"
    snapshot = GraphSnapshot(
        "r",
        {},
        "",
        [
            GraphNode(dynamic, NodeKind.CODE, "map", "r", {"dynamic": "true"}),
            GraphNode(dead_end, NodeKind.INTERFACE, "dead-end", "r"),
        ],
        [GraphEdge(dynamic, dead_end, EdgeKind.MAPS_TO, "AST", 0.9, "r", "repo://test#L1")],
    )
    assert analyze(snapshot, dynamic, "r").path == _independent_shortest_path(snapshot, dynamic)
