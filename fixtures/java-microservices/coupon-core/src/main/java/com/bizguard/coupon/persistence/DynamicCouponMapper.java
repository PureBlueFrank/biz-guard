package com.bizguard.coupon.persistence;

import java.util.Map;

/** Reflection-shaped mapper: static analysis must retain an UNKNOWN_BOUNDARY here. */
public final class DynamicCouponMapper {
    private DynamicCouponMapper() {}

    private static String map(Map<String, Object> values) {
        return String.valueOf(values.get("status"));
    }
}
