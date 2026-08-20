# mypy: disable-error-code=no-untyped-def
from pathlib import Path
from bizguard.graph.indexer import index
from bizguard.impact.analyzer import analyze


def test_five_layers():
    assert set(analyze(index(Path("fixtures/java-microservices"), "r"), "x", "r").layers) == {
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
    }


def test_capability_endpoint():
    assert (
        analyze(index(Path("fixtures/java-microservices"), "r"), "x", "r").path[-1]
        == "capability://coupon-redemption"
    )


def test_dynamic_unknown():
    assert analyze(
        index(Path("fixtures/java-microservices"), "r"), "DynamicCouponMapper", "r"
    ).unknown_boundary


def test_stale_unknown():
    assert analyze(index(Path("fixtures/java-microservices"), "r"), "x", "other").unknown_boundary


def test_evidence_has_contract_fields():
    e = analyze(index(Path("fixtures/java-microservices"), "r"), "x", "r").evidence[0]
    assert e.id and e.source and e.revision and e.evidence_uri
