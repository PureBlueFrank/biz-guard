---
id: ledger-invariant
title: Redemption ledger invariant
scope: coupon_redemption
owner: coupon_platform
source_uri: repo://coupon-core/src/main/java/com/bizguard/coupon/domain/CouponRedemption.java
source_revision: semantic-seed-v1
confidence: 0.98
security_label: internal
acl: [engineering]
status: published
policy_ids: [coupon-redemption-idempotency-key]
evidence_uri: db://coupon-core/coupon_redemption#status
---
Each successful redemption has one ledger transition and one idempotency key.
