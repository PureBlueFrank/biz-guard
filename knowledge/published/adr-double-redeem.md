---
id: adr-double-redeem
title: ADR prevent duplicate redemption
scope: coupon_redemption
owner: coupon_platform
source_uri: repo://coupon-core/src/main/java/com/bizguard/coupon/application/RedeemService.java
source_revision: semantic-seed-v1
confidence: 0.95
security_label: internal
acl: [engineering]
status: published
policy_ids: [coupon-redemption-idempotency-key]
evidence_uri: repo://coupon-core/src/main/java/com/bizguard/coupon/application/RedeemService.java
---
An upstream timeout can replay a redemption. Keep idempotency validation inside the transaction.
