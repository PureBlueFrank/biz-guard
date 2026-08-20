---
id: critical-redemption-policy
title: Critical redemption policy scope
scope: coupon_redemption
owner: coupon_platform
source_uri: repo://coupon-core/src/main/java/com/bizguard/coupon/application/RedeemService.java
source_revision: semantic-seed-v1
confidence: 1.0
security_label: internal
acl: [engineering]
status: published
policy_ids: [critical-coupon-redemption-idempotency-key]
evidence_uri: repo://coupon-core/src/main/java/com/bizguard/coupon/application/RedeemService.java
---
Critical Policy: coupon redemption changes mandate the idempotency protection policy.
