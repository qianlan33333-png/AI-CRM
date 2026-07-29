# AI Campaign Internal Events

AI Campaign internal events record campaign lifecycle business facts. They do
not mean that a broadcast task was created, a WeCom send happened, a Feishu
message was sent, or any external webhook was called.

## Event Schema

Canonical event types:

- `ai_campaign.created`
- `ai_campaign.approved`
- `ai_campaign.started`

Common fields:

- `aggregate_type`: `ai_campaign`
- `aggregate_id`: campaign code
- `subject_type`: `ai_campaign`
- `subject_id`: campaign code
- `source_module`: `cloud_orchestrator.campaigns_write`

Idempotency keys:

- `ai_campaign.created:{campaign_code}:created`
- `ai_campaign.approved:{campaign_code}:{approved_at_or_version}`
- `ai_campaign.started:{campaign_code}:{started_at_or_version}`

When the write model does not expose a timestamp or lifecycle version, approve
and start use stable lifecycle markers (`approved`, `started`). Repeated command
calls for the same campaign lifecycle fact therefore reuse the same internal
event instead of creating duplicates.

`payload_json` keeps only operational campaign fields needed for diagnostics,
such as campaign code, campaign id, status, review status, run status, operator,
target count, timestamps, trace id, and a safe metadata subset.

`payload_summary_json` is the admin-visible summary:

- `campaign_code`
- `status`
- `review_status`
- `run_status`
- `operator`
- `source`
- `target_count`
- `objective_present`
- `approved`
- `started`

It must not include customer lists, mobile numbers, raw external user ids,
openid, unionid, full prompts, full strategy text, tokens, or secrets.

## Feature Flag

Production default is off:

```bash
AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED=0
```

Emit requires all of:

- `AICRM_INTERNAL_EVENTS_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES` is empty or includes the relevant
  `ai_campaign.*` event type

Diagnostics expose `ai_campaign_internal_events_enabled`.

## Write Paths

The write path is `aicrm_next/extensions/growth/cloud_orchestrator/campaigns_write.py`.

- `CreateCloudCampaignCommand` emits `ai_campaign.created`
- `ApproveCloudCampaignCommand` emits `ai_campaign.approved`
- `StartCloudCampaignCommand` emits `ai_campaign.started`

The admin API routes are:

- `POST /api/admin/cloud-orchestrator/campaigns`
- `POST /api/admin/cloud-orchestrator/campaigns/{campaign_code}/approve`
- `POST /api/admin/cloud-orchestrator/campaigns/{campaign_code}/start`

Emit is wrapped by `safe_emit`. Internal event failures must not fail the
campaign write command.

## Consumers

Each AI Campaign event registers:

- `campaign_summary_consumer`
  Currently skipped with `reason=campaign_summary_not_configured`.
- `ai_campaign_ai_assist_notify_consumer`
  Currently skipped with
  `reason=ai_campaign_ai_assist_notify_not_configured`.
- `broadcast_task_planner_consumer`
  Currently skipped with `reason=broadcast_task_planner_not_configured`. It
  does not create or send real broadcast tasks in this slice.
- `audit_projection_consumer`
  Succeeds as a no-op audit projection with
  `reason=audit_projection_shadow_only`.

The AI assistant consumer uses an AI Campaign-specific name to avoid accidental
execution through generic consumer allowlists.

## External Safety

This slice never enables real external execution. It does not call WeCom group
send, WeCom private send, Feishu, webhooks, payment query, or refund APIs.

External Effects should remain disabled during production verification:

```bash
AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE=0
AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES=
```

## Worker Pair Allowlist

Do not add AI Campaign pairs to production
`AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS` during Q0/Q1.

Payment automation can continue with payment-only pairs:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS=payment.succeeded:order_projection_consumer,payment.succeeded:customer_business_summary_consumer,payment.succeeded:dnd_policy_consumer,payment.succeeded:ai_assist_notify_consumer,payment.succeeded:automation_payment_consumer
```

With pair-aware allowlisting, `ai_campaign.*` events may emit and remain pending
without being picked up by the worker.

## Production Verification

### Q0: Flag Off

1. Set `AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED=0`.
2. Create, approve, and start a safe test campaign.
3. Verify the write commands still succeed.
4. Verify no `ai_campaign.*` internal events or consumer runs are created.

### Q1: Shadow Emit

1. Set `AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED=1`.
2. Add `ai_campaign.created,ai_campaign.approved,ai_campaign.started` to
   `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES`.
3. Keep `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS` limited to payment
   pairs.
4. Create, approve, and start a safe test campaign.
5. Verify exactly one event for each lifecycle fact and four pending consumer
   runs per event.
6. Verify worker preview does not select AI Campaign consumers.

### Q2: Single Consumer

Use only the single-consumer endpoint, starting with:

```bash
POST /api/admin/internal-events/{event_id}/consumers/campaign_summary_consumer/run
```

Run dry-run first, then `dry_run=false` without `force`. Confirm:

- `status=succeeded` or `status=skipped` with a clear reason
- `attempt_count=1`
- `real_external_call_executed=false`
- no external effect attempt
- no broadcast task send

## Rollback

```bash
AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED=0
```

Remove `ai_campaign.*` from `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES`. Keep
payment, questionnaire, customer tag, and customer phone-bound configuration
unchanged.
