# mypy: disable-error-code=no-untyped-def
from pathlib import Path
from bizguard.analyzers.openapi_proto import analyze_openapi, analyze_proto

BASE = Path("fixtures/java-microservices/coupon-contract/src/main/resources")


def test_openapi_endpoint():
    assert any(
        x.kind == "endpoint" for x in analyze_openapi(BASE / "openapi.yaml", "coupon-contract", "r")
    )


def test_openapi_field():
    assert any(
        x.name == "status" for x in analyze_openapi(BASE / "openapi.yaml", "coupon-contract", "r")
    )


def test_openapi_location():
    assert all(
        x.line > 0 and x.column > 0
        for x in analyze_openapi(BASE / "openapi.yaml", "coupon-contract", "r")
    )


def test_proto_field():
    assert any(x.name == "status" for x in analyze_proto(BASE / "coupon.proto", "r"))


def test_proto_uri():
    assert all("#L" in x.evidence_uri for x in analyze_proto(BASE / "coupon.proto", "r"))
