# owner_migration.executed Internal Event

`owner_migration.executed` records the business fact that a customer owner
migration finished executing. It does not mean WeCom owners were changed,
webhooks were sent, Feishu was notified, or External Effects were dispatched.

## Event Schema

- `event_type`: `owner_migration.executed`
- `aggregate_type`: `owner_migration`
- `aggregate_id`: migration result id, batch id, or execution id
- `subject_type`: `owner_migration`
- `subject_id`: migration result id, batch id, or execution id
- `idempotency_key`: `owner_migration.executed:{migration_id_or_batch_id}`
- `source_module`: `owner_migration.application`
- `source_route`: `/api/admin/owner-migration/execute`

The payload keeps only safe operational diagnostics:

- migration id / batch id / execution id
- job/session safe references
- source and target owner present flags plus hash/ref values
- operator
- source route
- safe command/trace id
- customer count, success count, failed count, skipped count
- count consistency diagnostics and count source labels
- customer scope hash and present flag
- dry-run / real-execution indicators
- created/executed timestamps when present

`payload_summary_json` is the admin-list surface and contains:

- `migration_id` / `batch_id`
- `from_owner_present`, `from_owner_hash`
- `to_owner_present`, `to_owner_hash`
- `operator`
- `customer_count`
- `success_count`
- `failed_count`
- `skipped_count`
- `count_consistency`
- `count_source`
- `partial_failure_present`
- `all_failed`
- `source`
- `executed=true`

It must not include customer lists, raw `external_userid`, mobile numbers,
openid, unionid, webhook URLs, tokens, secrets, or failure details that embed
customer identifiers. Owner userids are represented as present flags and hashes
in the summary.

## Count Semantics

`success_count` means the migration has explicit success evidence. Failed
customers must not be counted as successful only because they appeared in the
requested customer scope, result rows, or overall `customer_count`.

The emitter calculates counts conservatively:

- `failed_count` is resolved before inferring success.
- Explicit success sources are, in order, `result.success_count`,
  `result.crm_updated`, `update_counts.contacts`, and
  `wecom_transfer.success_external_userids`.
- `touched_count` is used as a success source only when there is no failed
  count.
- If failures exist and no explicit success source exists, `success_count` is
  inferred as `max(0, customer_count - failed_count)`.
- If `failed_count >= customer_count` and there is no explicit upstream
  success count, `success_count` is `0`.
- If explicit success and failure counts conflict with `customer_count`, the
  explicit success count is preserved and `count_consistency` records the
  inconsistency for diagnosis.

Diagnostic fields:

- `count_consistency`: `ok` for consistent explicit counts; otherwise a
  conservative inference/correction label such as
  `inferred_from_customer_minus_failed`, `all_failed`, or
  `explicit_success_count_exceeds_customer_count_with_failures`.
- `count_source`: safe source labels for `customer_count`, `success_count`, and
  `failed_count`; these labels do not include customer identifiers.
- `partial_failure_present`: true whenever `failed_count > 0`.
- `all_failed`: true when the emitted summary represents a non-empty customer
  scope where every customer failed and no success was counted.

This prevents all-failed WeCom transfer or zero-row CRM updates from being
summarized as successful migrations.

## Feature Flag

Production default is off:

```bash
AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED=0
```

Emit requires all of:

- `AICRM_INTERNAL_EVENTS_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES` explicitly contains
  `owner_migration.executed`

`owner_migration.executed` uses the strict explicit allowlist gate. An empty
`AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES` value skips emits with:

```text
owner_migration_event_type_not_explicitly_allowed
```

Diagnostics exposes:

- `owner_migration_internal_events_enabled`

## Write Path

The emit path is the owner migration execution flow:

- `aicrm_next/crm/owner_migration/application.py`
- `OwnerMigrationService.execute_scoped`
- legacy `OwnerMigrationService._run_legacy` execute path

The migration executes first, then calls `safe_emit`. Internal event failures
are returned as `internal_event_status=failed` diagnostics, but they must not
fail the owner migration response.

## Consumers

New `owner_migration.executed` events fan out to four consumers:

- `customer_owner_projection_consumer`
  - Current behavior: succeeded/no-op.
  - Result: `customer_owner_projection=owner_migration_recorded`.
- `customer_summary_mark_dirty_consumer`
  - Current behavior: succeeded/no-op.
  - Result: `customer_summary_mark_dirty=owner_migration_recorded`.
- `owner_migration_ai_assist_notify_consumer`
  - Current behavior: skipped with
    `owner_migration_ai_assist_notify_not_configured`.
  - Uses an owner-migration-specific name to avoid shared consumer-name
    allowlist risk.
- `webhook_owner_migration_consumer`
  - Current behavior: skipped with `owner_migration_webhook_not_configured`.
  - Does not create an `external_effect_attempt` or send a webhook.

For compatibility with older pending runs, the registry also accepts the legacy
consumer name `ai_assist_notify_consumer` for `owner_migration.executed`. This
is a dispatch-only handler alias and returns skipped with:

```text
owner_migration_legacy_ai_assist_notify_not_configured
```

It is not part of the fan-out list for newly emitted events, so new
`owner_migration.executed` events create exactly the four consumers above.

No consumer performs real WeCom owner changes, WeCom messages, Feishu,
webhooks, payment queries, refunds, or External Effect dispatch.

## Worker Pair Allowlist

Do not add `owner_migration.executed:*` pairs during the initial shadow rollout.
Current payment-only auto-execute should remain pair-aware:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS=payment.succeeded:order_projection_consumer,payment.succeeded:customer_business_summary_consumer,payment.succeeded:dnd_policy_consumer,payment.succeeded:ai_assist_notify_consumer,payment.succeeded:automation_payment_consumer
```

With that configuration, `owner_migration.executed` can emit and create pending
consumer runs, but worker preview/run-due must return zero effective candidates
for owner-migration consumers. Manual single-consumer execution remains
available for approved gray checks.

## Production Verification

### Q0: Flag Off

1. Set `AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED=0`.
2. Keep `owner_migration.executed` out of allowed event types.
3. Execute a safe owner migration.
4. Confirm migration succeeds.
5. Confirm no `owner_migration.executed` event or consumer runs were created.
6. Confirm no external effect attempts or real external calls occurred.

### Q1: Shadow Emit

1. Set `AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED=1`.
2. Add `owner_migration.executed` to
   `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES`.
3. Keep `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS` payment-only.
4. Execute a safe owner migration.
5. Confirm exactly one `owner_migration.executed` event.
6. Confirm four pending consumer runs.
7. Confirm worker preview for owner-migration consumers is blocked by pair
   allowlist.

### Q2: Single-Consumer Gray

Use only the single-consumer endpoint:

```http
POST /api/admin/internal-events/{event_id}/consumers/customer_owner_projection_consumer/run
```

Use `dry_run=true` first, then `dry_run=false` with `force=false`.

Expected result is succeeded/no-op with
`customer_owner_projection=owner_migration_recorded`. No external effect
attempts, webhooks, WeCom calls, Feishu sends, or real owner changes should be
created by this verification.

## Rollback

If verification fails:

```bash
AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED=0
```

Remove `owner_migration.executed` from
`AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES`.

Keep other event-family flags and the payment-only pair allowlist unchanged.
