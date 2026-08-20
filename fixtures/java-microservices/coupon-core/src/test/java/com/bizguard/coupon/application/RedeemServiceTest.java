package com.bizguard.coupon.application;

import com.bizguard.coupon.api.CouponRequest;

public final class RedeemServiceTest {
    public static void main(String[] args) {
        String status = new RedeemService().redeem(new CouponRequest("SUMMER", "request-1")).status();
        assert "SUCCEEDED".equals(status);
    }
}
