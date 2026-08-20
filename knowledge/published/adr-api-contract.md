---
id: adr-api-contract
title: Redeem public API contract
scope: coupon_redemption
owner: coupon_platform
source_uri: repo://coupon-contract/src/main/resources/openapi.yaml
source_revision: semantic-seed-v1
confidence: 0.93
security_label: internal
acl: [engineering]
status: published
policy_ids: []
evidence_uri: repo://coupon-contract/src/main/resources/openapi.yaml
---
POST redeem is a public API and DTO field changes require contract compatibility testing.
