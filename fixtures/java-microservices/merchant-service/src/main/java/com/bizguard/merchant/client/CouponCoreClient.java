package com.bizguard.merchant.client;

import com.bizguard.merchant.api.CouponRedemptionDto;

/** Feign/RPC-shaped boundary without a network dependency. */
public interface CouponCoreClient {
    CouponRedemptionDto redeem(String couponCode, String idempotencyKey);
}
