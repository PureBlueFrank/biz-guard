from pathlib import Path
import shutil

from bizguard.eval.impact import _independent_shortest_path
from bizguard.graph.indexer import index
from bizguard.graph.models import EdgeKind, GraphEdge, GraphNode, GraphSnapshot, NodeKind
from bizguard.impact.analyzer import analyze
from bizguard.impact.service import ImpactService


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


def test_analysis_returns_a_shortest_path_for_every_reachable_terminal() -> None:
    start = "repo://coupon-core/Example.java#Example.status"
    boundary = "api://coupon-core/GET/status"
    capability = "capability://coupon-redemption"
    invariant = "invariant://status-compatible"
    snapshot = GraphSnapshot(
        "r",
        {},
        "",
        [
            GraphNode(start, NodeKind.CODE, "status", "r"),
            GraphNode(boundary, NodeKind.INTERFACE, "status", "r"),
            GraphNode(capability, NodeKind.BUSINESS, "redemption", "r"),
            GraphNode(invariant, NodeKind.BUSINESS, "compatible", "r"),
        ],
        [
            GraphEdge(start, boundary, EdgeKind.MAPS_TO, "AST", 0.9, "r", "repo://edge/1"),
            GraphEdge(boundary, capability, EdgeKind.BELONGS_TO_CAPABILITY, "catalog", 1.0, "r", "catalog://capability"),
            GraphEdge(boundary, invariant, EdgeKind.BELONGS_TO_CAPABILITY, "catalog", 1.0, "r", "catalog://invariant"),
        ],
    )
    result = analyze(snapshot, start, "r")
    assert {path[-1] for path in result.paths} == {capability, invariant}


def test_analysis_continues_from_capability_to_reachable_owner() -> None:
    start = "repo://coupon-core/Example.java#Example.status"
    capability = "capability://coupon-redemption"
    owner = "owner://coupon-platform"
    snapshot = GraphSnapshot(
        "r",
        {},
        "",
        [
            GraphNode(start, NodeKind.CODE, "status", "r"),
            GraphNode(capability, NodeKind.BUSINESS, "redemption", "r"),
            GraphNode(owner, NodeKind.ORGANIZATION, "coupon platform", "r"),
        ],
        [
            GraphEdge(start, capability, EdgeKind.MAPS_TO, "AST", 0.9, "r", "repo://edge/1"),
            GraphEdge(capability, owner, EdgeKind.OWNED_BY, "catalog", 1.0, "r", "catalog://owner"),
        ],
    )

    result = analyze(snapshot, start, "r")
    assert {path[-1] for path in result.paths} == {capability, owner}


def test_impact_service_invalidates_same_revision_snapshot_when_sources_change(
    tmp_path: Path,
) -> None:
    fixtures = tmp_path / "fixtures"
    shutil.copytree(ROOT, fixtures)
    service = ImpactService(fixtures)
    symbol = (
        "repo://coupon-core/src/main/java/com/bizguard/coupon/api/"
        "CouponResponse.java#CouponResponse.status"
    )
    service.analyze(symbol, "same-revision")
    before = service._snapshots["same-revision"].content_digest
    source = fixtures / "coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java"
    source.write_text(source.read_text(encoding="utf-8") + "\n// indexed change\n", encoding="utf-8")
    service.analyze(symbol, "same-revision")
    after = service._snapshots["same-revision"].content_digest

    assert before != after
