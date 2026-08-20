"""Regression tests for frozen graph identifier rules."""

import pytest

from bizguard.graph.ids import api_id, db_id, java_symbol, mq_id, proto_id, repo_id


def test_repo_id_is_deterministic_and_supports_java_method_symbols() -> None:
    expected = "repo://coupon-core/src/main/java/com/bizguard/coupon/CouponService.java#CouponService.redeem(CouponRequest)"
    assert (
        repo_id(
            "coupon-core",
            "src/main/java/com/bizguard/coupon/CouponService.java",
            "CouponService.redeem(CouponRequest)",
        )
        == expected
    )
    assert (
        repo_id(
            "coupon-core",
            "src/main/java/com/bizguard/coupon/CouponService.java",
            "CouponService.redeem(CouponRequest)",
        )
        == expected
    )


def test_api_id_has_stable_protocol_shape() -> None:
    assert (
        api_id("coupon-core", "post", "/v1/coupons/redeem", "redeemCoupon")
        == "api://coupon-core/POST/v1/coupons/redeem#redeemCoupon"
    )


def test_proto_id_has_stable_protocol_shape() -> None:
    assert (
        proto_id("coupon.v1", "CouponService", "Redeem") == "proto://coupon.v1/CouponService/Redeem"
    )


def test_db_id_has_stable_protocol_shape() -> None:
    assert (
        db_id("coupon-core", "coupon_redemption", "idempotency_key")
        == "db://coupon-core/coupon_redemption#idempotency_key"
    )


def test_mq_id_has_stable_protocol_shape() -> None:
    assert (
        mq_id("coupon-core", "coupon.redeemed", "CouponRedeemedEvent")
        == "mq://coupon-core/coupon.redeemed#CouponRedeemedEvent"
    )


def test_java_symbol_freezes_overload_safe_method_shape() -> None:
    assert (
        java_symbol("RedeemService", "redeem", ("CouponRequest",))
        == "RedeemService.redeem(CouponRequest)"
    )
    assert (
        java_symbol("DynamicCouponMapper", "map", ("java.util.Map",))
        == "DynamicCouponMapper.map(java.util.Map)"
    )


@pytest.mark.parametrize(
    "factory,args", [(repo_id, ("coupon-core", "../secret")), (api_id, ("coupon-core", "GET", ""))]
)
def test_ids_reject_invalid_path_inputs(factory: object, args: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        factory(*args)  # type: ignore[operator]
