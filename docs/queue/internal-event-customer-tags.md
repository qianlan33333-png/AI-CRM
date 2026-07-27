# Customer Tag Internal Event Queue

## Scope

This runbook covers the `customer.tagged` and `customer.untagged` internal event vertical slice.

These events record the business fact that a customer tag mutation command was accepted and queued for the External Effect worker. They do not mean the external WeCom tag API has already been called.

## Feature Flags

- `AICRM_INTERNAL_EVENTS_ENABLED=1` enables the Internal Event Queue infrastructure.
- `AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED=1` enables customer tag shadow emit.
- `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES=customer.tagged,customer.untagged` must include the target event type.
- `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS` should remain limited to approved pairs, usually `payment.succeeded:<consumer>` pairs only during customer tag Q1/Q2.
- `AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS` should not include customer tag consumers until a specific worker gray stage is approved.
- `AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE=0` and `AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES=` keep external effect execution disabled.

Production default is safe: `AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED=0`.

## Worker Pair Allowlist

Customer tag events share generic consumer names with other event families. For example, `ai_assist_notify_consumer` can be registered for both `payment.succeeded` and `customer.tagged`.

For production auto-execute, consumer-name-only allowlists are not precise enough. Use `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS` to allow exact `event_type:consumer_name` pairs:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS=payment.succeeded:order_projection_consumer,payment.succeeded:customer_business_summary_consumer,payment.succeeded:dnd_policy_consumer,payment.succeeded:ai_assist_notify_consumer,payment.succeeded:automation_payment_consumer
```

With the configuration above, `customer.tagged` and `customer.untagged` can emit and create pending consumer runs, but all customer tag consumers remain blocked by the pair allowlist. This prevents a shared consumer such as `ai_assist_notify_consumer` from being auto-executed for customer tags just because it is allowed for payment.

## Event Schema

Canonical event types:

- `customer.tagged`
- `customer.untagged`

Aggregate:

- `aggregate_type=customer`
- `aggregate_id=<external_userid or stable customer id from the tag command>`

Subject:

- `subject_type=customer`
- `subject_id=<redacted customer identifier>`

Idempotency:

- `customer.tagged:{command.idempotency_key or command_id}`
- `customer.untagged:{command.idempotency_key or command_id}`

Payload:

- `external_userid`
- `tag_ids`
- `tag_count`
- `effect_type`
- `source_context`
- `source`
- `side_effect_plan`
- `external_effect_job`

Payload summary:

- `external_userid_redacted`
- `tag_count`
- `tag_ids_count`
- `source`
- `effect_type`

The event detail API may keep raw identifiers inside `payload_json` for backend diagnosis, but list/detail views must default to `payload_summary_json` and the redacted subject fields.

## Consumers

`tag_external_effect_shadow_consumer`

- Reuses the side-effect plan or shadow external effect job already created by `customer_tags.live_mutation`.
- Does not call WeCom.
- Does not create `external_effect_attempt`.
- Returns `succeeded` when an existing plan/job is present.
- Returns `skipped` with `customer_tag_external_effect_not_configured_or_already_shadow_only` when there is no actionable plan.

`tag_summary_consumer`

- Placeholder for customer summary refresh.
- Currently returns `skipped` with `customer_tag_summary_not_configured`.

`ai_assist_notify_consumer`

- Placeholder for future internal AI-assist notification.
- Currently returns `skipped` with `ai_assist_notify_not_configured`.

## Compatibility With Live Mutation

The current write path is `aicrm_next/crm/customer_tags/live_mutation.py`.

That path creates a queued side-effect plan and a queued `external_effect_job` for WeCom tag mark/unmark. The internal event is emitted after those planning steps through `safe_emit`, so emit failures do not break the customer tag mutation path.

The consumer receives sanitized references to the existing side-effect plan and external effect job and reuses them. This avoids duplicate external effect planning.

## Submit-Time External Call Safety

This slice does not execute real WeCom tag operations during the mutation command or internal-event consumer. Real WeCom mutation can only happen later through the External Effect worker after payload and runtime gates pass.

Safety guarantees:

- `live_mutation` uses `adapter_mode=queued_external_effect`.
- External effect jobs are queued with `execution_mode=execute`, `status=queued`, and `requires_approval=false` for single-customer tag effects.
- The customer tag consumer does not dispatch external effects.
- No `external_effect_attempt` is created by the internal event consumer.
- The External Effect worker still enforces Push Center capability gates for queued customer-tag jobs. Questionnaire H5 `final_tags` no longer relies on a queued job or `bypass_push_capability`; submit-time code calls WeCom mark_tag directly and records explicit `tag_apply` success/failure.

## Production Verification

### Q0: Deployed, Flag Off

Set:

```bash
AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED=0
```

Verify:

- Customer tag mutation still succeeds.
- No `customer.tagged` or `customer.untagged` event is created.
- No customer tag consumer runs are created.
- No real WeCom call occurs.

### Q1: Shadow Emit

Set:

```bash
AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED=1
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES=payment.succeeded,questionnaire.submitted,customer.tagged,customer.untagged
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS=payment.succeeded:order_projection_consumer,payment.succeeded:customer_business_summary_consumer,payment.succeeded:dnd_policy_consumer,payment.succeeded:ai_assist_notify_consumer,payment.succeeded:automation_payment_consumer
```

Keep:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS=<payment consumers only>
AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE=0
AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES=
```

Verify one safe tag mutation:

- Exactly one `customer.tagged` or `customer.untagged` event.
- Three consumer runs are created:
  - `tag_external_effect_shadow_consumer`
  - `tag_summary_consumer`
  - `ai_assist_notify_consumer`
- Consumer runs remain pending.
- Worker preview for payment consumers does not include customer tag events.
- Explicit customer tag preview returns zero candidates or reports work blocked by pair allowlist until customer tag pairs are approved.

### Q2: Single Consumer Gray

Use only:

```http
POST /api/admin/internal-events/{event_id}/consumers/tag_external_effect_shadow_consumer/run
```

Body:

```json
{
  "dry_run": false,
  "force": false,
  "reason": "customer_tag_q2_single_consumer_gray"
}
```

Verify:

- Status is `succeeded` or an explicit `skipped`.
- Existing side-effect plan or shadow external effect job is reused.
- No duplicate `external_effect_job`.
- No `external_effect_attempt`.
- No real WeCom API call.

## Rollback

Disable customer tag emit:

```bash
AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED=0
```

Remove customer tag events from:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES
```

Keep or restore the payment-only pair allowlist:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS=payment.succeeded:order_projection_consumer,payment.succeeded:customer_business_summary_consumer,payment.succeeded:dnd_policy_consumer,payment.succeeded:ai_assist_notify_consumer,payment.succeeded:automation_payment_consumer
```

Keep the internal event rows and attempts for diagnosis. Schema rollback is not required.
