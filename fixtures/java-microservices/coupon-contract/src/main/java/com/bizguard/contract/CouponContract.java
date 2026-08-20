package com.bizguard.contract;

/** Marker type proving this contract-only module compiles independently. */
public final class CouponContract {
    private CouponContract() {}

    public static boolean isKnownStatus(String status) {
        return "PENDING".equals(status) || "SUCCEEDED".equals(status) || "FAILED".equals(status);
    }
}
