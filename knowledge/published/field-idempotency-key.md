---
id: field-idempotency-key
title: idempotency_key request field
scope: coupon_redemption
owner: coupon_platform
source_uri: repo://coupon-core/src/main/java/com/bizguard/coupon/api/CouponRequest.java
source_revision: semantic-seed-v1
confidence: 1.0
security_label: internal
acl: [engineering]
status: published
policy_ids: [coupon-redemption-idempotency-key]
evidence_uri: repo://coupon-core/src/main/java/com/bizguard/coupon/api/CouponRequest.java
---
The DTO field idempotency_key defines the retry boundary for redeem requests.
