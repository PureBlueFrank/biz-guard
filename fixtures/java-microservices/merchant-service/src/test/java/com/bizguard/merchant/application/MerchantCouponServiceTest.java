package com.bizguard.merchant.application;

import com.bizguard.merchant.api.CouponRedemptionDto;

public final class MerchantCouponServiceTest {
    public static void main(String[] args) {
        MerchantCouponService service = new MerchantCouponService(
                (couponCode, idempotencyKey) -> new CouponRedemptionDto("redemption-1", "SUCCEEDED"));
        assert service.redeemable("SUMMER", "request-1");
    }
}
