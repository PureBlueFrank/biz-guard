package com.bizguard.coupon.application;

import com.bizguard.coupon.api.CouponRequest;
import com.bizguard.coupon.api.CouponResponse;
import com.bizguard.coupon.domain.CouponRedemption;
import com.bizguard.coupon.messaging.CouponRedeemedProducer;
import com.bizguard.coupon.persistence.CouponRedemptionRepository;
import org.springframework.stereotype.Service;

@Service
public final class RedeemService {
    private final CouponRedeemedProducer producer = new CouponRedeemedProducer();
    private final CouponRedemptionRepository repository = new CouponRedemptionRepository();

    public CouponResponse redeem(CouponRequest request) {
        CouponRedemption redemption = new CouponRedemption(request.idempotencyKey(), "PENDING");
        try {
            redemption = repository.save(new CouponRedemption(request.idempotencyKey(), "SUCCEEDED"));
            producer.publish(redemption);
            return new CouponResponse(redemption.idempotencyKey(), redemption.status());
        } catch (RuntimeException failure) {
            repository.save(new CouponRedemption(redemption.idempotencyKey(), "FAILED"));
            throw failure;
        }
    }
}
