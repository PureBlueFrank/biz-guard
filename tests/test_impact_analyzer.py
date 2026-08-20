from pathlib import Path

from bizguard.graph.indexer import index
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
    assert not analyze(snapshot, "repo://coupon-core/DynamicCouponMapperWhatever", "r").unknown_boundary


def test_path_evidence_is_taken_from_traversed_edges() -> None:
    snapshot = index(ROOT, "r")
    changed = "repo://coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java#CouponResponse.status"
    result = analyze(snapshot, changed, "r")
    edge_uris = {edge.evidence_uri for edge in snapshot.edges}
    assert result.evidence
    assert all(item.evidence_uri in edge_uris for item in result.evidence)
