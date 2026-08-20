---
id: postmortem-double-redeem
title: 重复核销故障复盘
type: postmortem
scope: coupon-service
source: incidents/INC-2025-021.md
revision: semantic-seed-v1
source_commit: 4165ccf0fb6b88705b7e6fca4a79aa144d4c1ada
owner: payments-platform
policy_ids:
  - redeem-must-check-idempotency-in-transaction
---

一次上游超时触发的自动重试曾造成重复核销风险。复盘结论是将可追溯的保护措施交由引用的 Policy ID 约束，并保留完整账本审计。
