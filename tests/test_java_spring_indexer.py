# mypy: disable-error-code=no-untyped-def
from pathlib import Path
from bizguard.analyzers.java_spring import analyze

ROOT = Path("fixtures/java-microservices/coupon-core/src/main/java/com/bizguard/coupon")


def test_class_position():
    assert any(
        x.kind == "class" and x.line > 0
        for x in analyze(ROOT / "api/CouponController.java", "coupon-core", "r")
    )


def test_method_position():
    assert any(
        x.kind == "method" and x.name == "redeem"
        for x in analyze(ROOT / "api/CouponController.java", "coupon-core", "r")
    )


def test_call_position():
    assert any(
        x.kind == "call" and x.name == "redeem"
        for x in analyze(ROOT / "api/CouponController.java", "coupon-core", "r")
    )


def test_field_position():
    assert any(
        x.kind == "field" and x.name == "service"
        for x in analyze(ROOT / "api/CouponController.java", "coupon-core", "r")
    )


def test_uri_has_location():
    assert all(
        "#L" in x.evidence_uri
        for x in analyze(ROOT / "api/CouponResponse.java", "coupon-core", "r")
    )
