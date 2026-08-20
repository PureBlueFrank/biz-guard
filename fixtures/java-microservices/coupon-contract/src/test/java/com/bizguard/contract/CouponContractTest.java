package com.bizguard.contract;

public final class CouponContractTest {
    public static void main(String[] args) {
        assert CouponContract.isKnownStatus("FAILED");
        assert !CouponContract.isKnownStatus("UNKNOWN");
    }
}
