# WeChat Pay Order Identity Repair

## Status

Retired.

`POST /api/admin/jobs/order-identity-repair/run` is kept only as an authenticated
410 endpoint so stale cron/admin callers fail closed with
`order_identity_repair_retired`. It no longer reads or writes order identity
data, and it makes no external calls.

The retired ledger table `wechat_pay_order_identity_repair` is dropped by
Alembic revision `0091_retire_wechat_pay_order_identity_repair`.

## Current Path

Paid order customer identity must be handled by the current order/customer
identity projection path. Do not reintroduce
`aicrm_next.extensions.commerce.commerce.order_identity_repair` or schedule a replacement repair job
without a new architecture review.

## Stale Caller Cleanup

1. Remove any hourly caller still posting to
   `/api/admin/jobs/order-identity-repair/run`.
2. Use the 410 response body to confirm callers have stopped relying on the
   retired repair job.
3. For historical order investigations, query `wechat_pay_orders` and the
   current customer identity projection source directly.

## Rollback

Rollback is a previous release rollback plus the Alembic downgrade that recreates
the retired table. Do not treat this runbook as approval to resume the repair
job in the current release.
