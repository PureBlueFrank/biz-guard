package com.bizguard.coupon.persistence;

import com.bizguard.coupon.domain.CouponRedemption;

/** Deliberately small repository-shaped persistence boundary for fixture analysis. */
public final class CouponRedemptionRepository {
    public CouponRedemption save(CouponRedemption redemption) {
        return redemption;
    }
}
