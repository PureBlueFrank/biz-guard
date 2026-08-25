# mypy: disable-error-code=no-untyped-def
from pathlib import Path
import pytest
from bizguard.graph.indexer import index
from bizguard.graph.store import GraphStore
from bizguard.semantic.models import Capability, Invariant, Owner, SemanticCatalog


def test_snapshot_has_nodes():
    assert index(Path("fixtures/java-microservices"), "r").nodes


def test_snapshot_has_edges():
    assert index(Path("fixtures/java-microservices"), "r").edges


def test_snapshot_round_trip(tmp_path: Path):
    s = index(Path("fixtures/java-microservices"), "r")
    p = tmp_path / "g.json"
    GraphStore(p).save(s)
    assert GraphStore(p).load("r").revision == "r"


def test_snapshot_rejects_stale(tmp_path: Path):
    p = tmp_path / "g.json"
    GraphStore(p).save(index(Path("fixtures/java-microservices"), "r"))
    with pytest.raises(ValueError, match="INDEX_LAG"):
        GraphStore(p).load("new")


def test_snapshot_reports_actual_edge_kind_coverage():
    kinds = {edge.kind.value for edge in index(Path("fixtures/java-microservices"), "r").edges}
    assert {"CALLS", "MAPS_TO", "PUBLISHES", "CONSUMES", "EXPOSES", "SERIALIZES_TO"} <= kinds


def test_evidence_locations_match_fixture_source_lines():
    edges = index(Path("fixtures/java-microservices"), "r").edges
    evidence_uris = {edge.evidence_uri for edge in edges}
    assert "repo://coupon-contract/src/main/resources/openapi.yaml#L31:C9" in evidence_uris
    assert "repo://coupon-core/src/main/java/com/bizguard/coupon/domain/CouponRedemption.java#L8:C5" in evidence_uris


def test_business_graph_nodes_come_from_organization_catalog(tmp_path: Path):
    repository = tmp_path / "payments-service/src/main/java/example"
    repository.mkdir(parents=True)
    (repository / "PaymentService.java").write_text(
        "package example; class PaymentService { void capture() {} }\n",
        encoding="utf-8",
    )
    catalog = SemanticCatalog(
        schema_version=1,
        revision="payments-v1",
        capabilities=[
            Capability(
                id="payment_capture",
                name="Payment capture",
                owner="payments_team",
                repositories=["payments-service"],
                graph=True,
            )
        ],
        owners=[
            Owner(id="payments_team", name="Payments Team", repositories=["payments-service"])
        ],
        entities=[],
        states=[],
        invariants=[
            Invariant(
                id="capture_once",
                capability="payment_capture",
                owner="payments_team",
                statement="A payment is captured once.",
                source_id="repo://payments-service/PaymentService.java",
            )
        ],
        policies=[],
        required_tests=[],
    )
    snapshot = index(tmp_path, "payments-r1", catalog)
    identifiers = {node.id for node in snapshot.nodes}
    assert "capability://payment-capture" in identifiers
    assert "owner://payments-team" in identifiers
    assert "invariant://capture-once" in identifiers
    assert "capability://coupon-redemption" not in identifiers
