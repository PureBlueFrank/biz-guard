package com.bizguard.coupon.messaging;

import com.bizguard.coupon.domain.CouponRedemption;

public final class CouponRedeemedProducer {
    public void publish(CouponRedemption redemption) { /* fixture MQ topic: coupon.redeemed */ }
}
