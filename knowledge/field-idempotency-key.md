---
id: field-idempotency-key
title: 幂等键字段语义
type: field-card
scope: coupon-service
source: schemas/coupon-redemption-request.json
owner: payments-platform
policy_ids:
  - redeem-must-check-idempotency-in-transaction
---

`idempotency_key` 标识同一业务请求的重试边界。它由调用方携带，供核销链路关联重复请求和审计记录。
