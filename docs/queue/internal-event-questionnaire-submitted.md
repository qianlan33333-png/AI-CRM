# questionnaire.submitted Internal Event

`questionnaire.submitted` records the business fact that a public H5 questionnaire
submission was accepted and persisted. It does not mean a webhook was delivered,
or automation was executed. The H5 submit handler itself is responsible for
synchronous questionnaire `final_tags` application: it calls the real WeCom
`/cgi-bin/externalcontact/mark_tag` API when tags, `external_userid`,
`follow_user_userid`, and WeCom config are present. Local CRM tag rows are only a
post-success mirror.

## Event Schema

- `event_type`: `questionnaire.submitted`
- `aggregate_type`: `questionnaire_submission`
- `aggregate_id`: `submission_id`
- `idempotency_key`: `questionnaire.submitted:{submission_id}`
- `source_module`: `questionnaire.h5_write`
- `trace_id`: the submit command trace id

Payload keeps the fields needed by consumers:

- `questionnaire`: id, slug, title, and external push configuration.
- `submission`: submission id, questionnaire id, slug, respondent key,
  external userid, identity presence flags, submitted timestamp, score, and
  answer count.
- `answer_snapshots`: raw answer snapshots for downstream payload builders.
- `source`: source route, trace id, and command id.

Admin list/detail views must use `payload_summary_json` and redacted summaries.
They must not expose raw answers, full phone numbers, openid, unionid, tokens, or
secrets.

## Payload Summary

`payload_summary_json` is intentionally small:

- `questionnaire_id`
- `slug`
- `submission_id`
- `external_userid_present`
- `mobile_present`
- `answer_count`
- `score`
- `final_tag_count`

## Consumers

- `questionnaire_projection_consumer`
  Confirms the submission id exists in the event payload and returns
  `questionnaire_projection=submitted_confirmed`. There is no read-model refresh
  yet.

- `questionnaire_webhook_consumer`
  Creates or reuses a `WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH`
  `external_effect_job`. The submit path queues webhook jobs with
  `execution_mode=execute`, `status=queued`, and `requires_approval=false`, but
  neither the submit handler nor this consumer dispatches the external effect.

- `questionnaire_tag_consumer`
  Currently skips with
  `questionnaire_tag_side_effect_already_planned_or_not_configured`. Existing H5
  submit tag handling remains in place: submit-time code has already attempted
  the real WeCom mark_tag call and returned `tag_apply.status`; this consumer
  must not treat queued/planned state as tag success.

- `automation_questionnaire_consumer`
  Currently skips with `automation_questionnaire_not_configured`.

- `customer_summary_consumer`
  Currently skips with `customer_summary_not_configured`.

## Feature Flags

- `AICRM_INTERNAL_EVENTS_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES=payment.succeeded,questionnaire.submitted`

`AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED` defaults to off. Turning it off
stops new `questionnaire.submitted` emits without changing the legacy
questionnaire submit path.

## Compatibility

The existing H5 submit path in `aicrm_next/extensions/forms/questionnaire/h5_write.py` remains in
place:

- existing questionnaire external push planning is not removed;
- existing tag apply response fields are preserved, but tag success is now tied
  to the real WeCom mark_tag result and local projection is only a post-success
  mirror;
- submit API response fields are preserved.

The webhook consumer deduplicates against existing jobs using:

- `effect_type=webhook.questionnaire_submission.push`
- `target_type=questionnaire_submission`
- `target_id=submission_id`
- `business_type=questionnaire`
- `business_id=questionnaire_id`

If a matching job exists, the consumer returns
`external_effect_job_reused=true` and does not create a second job. If no job
exists and external push is configured, it creates exactly one queued
job.

## External Call Safety

The internal event worker does not call webhooks, WeCom, Feishu, payment query,
or refund APIs. External work must be represented as an `external_effect_job`.
This event slice only plans or reuses `external_effect_job` rows.

Keep External Effects real execution disabled during questionnaire rollout:

```bash
AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE=0
AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES=
```

## Production Verification

### Q0: deploy with flag off

```bash
AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED=0
```

Verify questionnaire submissions still succeed and no
`questionnaire.submitted` events are created.

### Q1: shadow emit

```bash
AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED=1
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES=payment.succeeded,questionnaire.submitted
```

Keep worker `allowed_consumers` limited to payment consumers. Submit one test
questionnaire and verify:

- exactly one `questionnaire.submitted`;
- five consumer runs are created;
- no questionnaire consumer is auto-executed;
- admin list/detail do not expose raw answers or PII.

### Q2: single-consumer gray

Use only the single-consumer endpoint:

```bash
POST /api/admin/internal-events/{event_id}/consumers/questionnaire_projection_consumer/run
POST /api/admin/internal-events/{event_id}/consumers/questionnaire_webhook_consumer/run
```

Run dry-run first, then `dry_run=false` with `force=false`. Verify the webhook
consumer creates or reuses a shadow/planned external effect job and no
`external_effect_attempt` is created.

### Q3: worker allowlist gray

Requires a separate approval. Do not add questionnaire consumers to production
auto-execute allowlists in this PR.

## Rollback

Rollback is configuration-only:

```bash
AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED=0
```

Remove `questionnaire.submitted` from
`AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES` if desired. Existing
`internal_event`, `internal_event_consumer_run`, and attempt rows should remain
for diagnosis. This rollback does not affect `payment.succeeded`.
