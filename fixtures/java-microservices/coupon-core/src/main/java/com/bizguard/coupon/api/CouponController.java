package com.bizguard.coupon.api;

import com.bizguard.coupon.application.RedeemService;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public final class CouponController {
    private final RedeemService service = new RedeemService();

    @PostMapping("/v1/coupons/redeem")
    public CouponResponse redeem(CouponRequest request) {
        return service.redeem(request);
    }
}
