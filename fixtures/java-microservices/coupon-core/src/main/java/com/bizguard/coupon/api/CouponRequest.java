package com.bizguard.coupon.api;

public record CouponRequest(String couponCode, String idempotencyKey) {}
