package com.bizguard.merchant.application;

import com.bizguard.merchant.api.CouponRedemptionDto;
import com.bizguard.merchant.client.CouponCoreClient;

/** Consumer of the public status field returned by coupon-core. */
public final class MerchantCouponService {
    private final CouponCoreClient client;

    public MerchantCouponService(CouponCoreClient client) { this.client = client; }

    public boolean redeemable(String couponCode, String idempotencyKey) {
        CouponRedemptionDto response = client.redeem(couponCode, idempotencyKey);
        return "SUCCEEDED".equals(response.status());
    }
}
