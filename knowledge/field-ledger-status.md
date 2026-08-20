---
id: field-ledger-status
title: 账本状态字段语义
type: field-card
scope: coupon-service
source: schemas/coupon-ledger-entry.json
revision: semantic-seed-v1
source_commit: 4165ccf0fb6b88705b7e6fca4a79aa144d4c1ada
owner: payments-platform
policy_ids:
  - redeem-must-check-idempotency-in-transaction
---

`ledger_status` 描述账本条目的业务状态，用于运营查询和核销结果审计。状态含义不替代所引用 Policy ID 的代码检查。
