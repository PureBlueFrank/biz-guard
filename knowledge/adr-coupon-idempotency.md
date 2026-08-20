---
id: adr-coupon-idempotency
title: 优惠券核销幂等性约束
type: adr
scope: coupon-service
source: docs/adr/ADR-001.md
owner: payments-platform
policy_ids:
  - redeem-must-check-idempotency-in-transaction
---

重复提交可能来自客户端重试、网关超时或消息重放。该风险的可判定保护由所引用的 Policy ID 定义；本记录保留采用该保护的业务背景。
