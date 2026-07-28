# Broadcast Jobs Queue Contract

`broadcast_jobs` is the durable business-intent queue for scheduled broadcast
sends. After the scheduler consolidation cutover,
`aicrm-job-catalog-scheduler.timer` is its only automatic clock owner and invokes
the versioned `campaign.plan` handler. For ordinary and historical rows, the
Next-native worker converts supported rows into `external_effect_job` records
in one database transaction. For an approved `agent_generated_single`
AI-assistant recipient, approval creates the `broadcast_jobs` owner row, its
`external_effect_job`, projection state, audit event, and durable PostgreSQL
wake signal in the same transaction. The catalog scheduler remains a
leak-reconciliation path for rows that could not be materialized at approval
time; neither path owns the provider call. The persistent External Effect
runtime remains the only WeCom delivery owner.

AI-assistant private effects use `wecom_ai_assistant_bulk`. Ordinary private
broadcasts stay on `wecom_bulk`; welcome and interactive traffic therefore do
not lose their lane capacity during an AI burst. Both bulk lanes use the same
`rate_scope_key` and process-level 2 starts/second, burst-2 limiter, while the
existing durable cooldown remains authoritative for 429/45009/45011 across
processes.

`openclaw-broadcast-queue-worker.timer` and the intermediate
`aicrm-next-broadcast-delegation.timer` are retired owners. They must stay
disabled and must not be restored as fallbacks.

## Field Roles

- `source_type`: low-level handler key. The worker dispatches by this value.
- `business_domain`: product-level source family: `automation_ops`, `ai_assistant`, `group_ops`, `manual`, or `unknown`.
- `channel`: destination channel, such as `wecom_private` or `wecom_customer_group`.
- `target_kind`: target identity shape: `external_userid`, `chat_id`, `mixed`, `dynamic`, or `unknown`.
- `idempotency_key`: stable duplicate-prevention key. Do not use random values.
- `failure_type`: safe failure classification for future retry policy. The queue does not auto-retry external side effects.
- `retry_policy_json` and `metadata_json`: reserved extension fields. Do not store customer privacy, full message payloads, tokens, or webhook URLs here.

## New Business Intake Checklist

- [ ] Choose the product `business_domain`.
- [ ] Choose the handler `source_type`.
- [ ] Choose the destination `channel`.
- [ ] Choose the `target_kind`.
- [ ] Define a stable `idempotency_key`.
- [ ] Define the `content_payload` schema.
- [ ] Route supported delegation through the Next broadcast queue worker in `aicrm_next/automation/background_jobs/broadcast_queue_worker.py`.
- [ ] For AI-assistant recipient approval, attempt transactional immediate materialization and retain scheduler reconciliation when validation cannot materialize it.
- [ ] Keep unsupported `source_type` values safely skipped until a Next-native dispatcher path is reviewed.
- [ ] Ensure the dispatcher path is safe around external side effects and resume cases.
- [ ] Use `enqueue_broadcast_job(...)` or pass the standard metadata through `enqueue_job(...)`.
- [ ] Add targeted tests for intake, duplicate handling, status transitions, and event audit.

## Handler Contract

Handlers receive one job dict and return one of:

```python
{"ok": True, "sent_count": 1, "failed_count": 0, "outbound_task_id": 123}
{"ok": False, "error": "safe short reason"}
```

The Broadcast worker owns claim, planning, and delegation state transitions.
The External Effect runtime owns provider attempts and final delivery truth.

For private effects, `ordering_key = external_contact:<external_userid>` keeps
one customer's calls strictly ordered. Fairness uses
`broadcast:<sender_userid>:<batch_key>` so different senders and batches rotate
without combining message contents.

Do not blindly retry sends with unknown external side effects. Future retry policy can use `failure_type`, for example:

- `before_external_call`: potentially safe to retry.
- `external_call_failed_known`: retry only by known error policy.
- `external_call_unknown`: manual reconciliation first.
- `validation_failed`: do not retry.
- `handler_error`: inspect before retrying.

## Group Ops Compatibility

Group operations plans can continue using `source_type = "workflow"` so existing handler routing remains stable. They are classified as `business_domain = "group_ops"` when `source_table = "automation_group_ops_plans"` or `content_payload.channel = "wecom_customer_group"`.

This PR intentionally does not add a new `group_ops` `source_type`: the current workflow handler already contains the customer-group dispatch branch, and changing the DB check plus registry key would increase rollout risk for old queued jobs.
