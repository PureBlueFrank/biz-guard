package com.bizguard.coupon.domain;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;

@Entity
public final class CouponRedemption {
    @Id private final String idempotencyKey;
    private final String status;
    public CouponRedemption(String idempotencyKey, String status) { this.idempotencyKey = idempotencyKey; this.status = status; }
    public String idempotencyKey() { return idempotencyKey; }
    public String status() { return status; }
}
