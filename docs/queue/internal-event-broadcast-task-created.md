# broadcast_task.created Internal Event

`broadcast_task.created` records the business fact that a broadcast, group-send,
or private-send task was created. It does not mean the task was approved,
dispatched, sent to WeCom, sent to Feishu, pushed to a webhook, or executed by
External Effects.

## Event Schema

- `event_type`: `broadcast_task.created`
- `aggregate_type`: `broadcast_task`
- `aggregate_id`: broadcast task id or code
- `subject_type`: `broadcast_task`
- `subject_id`: broadcast task id or code
- `idempotency_key`: `broadcast_task.created:{task_id_or_code}`

The payload keeps only operational diagnostics. Redaction applies to the stored
`payload_json`, not only to admin summaries or API view models:

- task id / task code
- source type, module, route, and safe source reference
- related campaign code when present
- related ops plan id when present
- task type and send channel
- target count / audience count
- created by / operator
- scheduled time
- task status
- trace id / command id
- boolean or count-only indicators for content and target list presence

### Stored Payload Redaction

Some creation paths, especially the group-ops private-message enqueue path, can
receive `source_id`, `trace_id`, or `idempotency_key` values that embed a raw
`external_userid` or other customer identifier. `broadcast_task.created` must
never persist those raw values in `internal_event.payload_json`,
`internal_event.payload_summary_json`, `internal_event.trace_id`,
`internal_event.correlation_id`, or `internal_event.source_command_id`.

The emit helper stores source references as follows:

- `payload.broadcast_task.source_id`: `source_ref:{sha256[:16]}` when a source
  id is present, otherwise empty.
- `payload.broadcast_task.source_id_redacted`: same safe reference.
- `payload.broadcast_task.source_id_hash`: the first 16 hex characters of a
  SHA-256 hash.
- `payload.broadcast_task.source_id_present`: boolean indicator only.
- `payload.broadcast_task.related_ops_plan_id`: `ops_plan_ref:{sha256[:16]}`
  when a related ops plan id is present.
- `payload.broadcast_task.related_ops_plan_ref`: same safe reference.
- `payload.broadcast_task.related_ops_plan_hash`: the first 16 hex characters
  of a SHA-256 hash.
- `payload.broadcast_task.related_ops_plan_present`: boolean indicator only.
- `payload.broadcast_task.command_id`: `broadcast_task.created:{task_id}`.
- `payload.broadcast_task.trace_id`: `broadcast_task.created:{task_id}`.
- `payload.broadcast_task.original_trace_ref`: `trace_ref:{sha256[:16]}`
  when original trace input is present.
- `payload.broadcast_task.original_trace_present`: boolean indicator only.
- `payload.broadcast_task.original_trace_hash`: the first 16 hex characters of
  a SHA-256 hash when raw trace input is present.
- `payload.broadcast_task.trace_id_present`: boolean indicator only.
- `payload.broadcast_task.trace_id_hash`: the first 16 hex characters of a
  SHA-256 hash when raw trace input is present. This is a compatibility alias
  for `original_trace_hash`.
- `payload.broadcast_task.original_idempotency_key_present`: boolean indicator
  only.
- `payload.broadcast_task.original_idempotency_key_hash`: the first 16 hex
  characters of a SHA-256 hash when raw idempotency input is present.
- `payload.broadcast_task.idempotency_key_present`: boolean indicator only.
- `payload.broadcast_task.idempotency_key_hash`: the first 16 hex characters of
  a SHA-256 hash when raw idempotency input is present. This is a compatibility
  alias for `original_idempotency_key_hash`.
- `trace_id`: `broadcast_task.created:{task_id}`.
- `correlation_id`: `broadcast_task.created:{task_id}`.
- `source_command_id`: `broadcast_task.created:{task_id}`.

If trace, correlation, command, or batch fallback data is missing, the emit path
falls back to the broadcast task id rather than raw `source_id`, raw `trace_id`,
or raw `idempotency_key`. This prevents raw `external_userid`, mobile numbers,
openid, unionid, webhook URLs, tokens, or message text from being stored in the
event record while keeping hash/present flags for diagnostics.

### Safe Trace Lookup

After trace redaction, raw upstream trace values such as a cloud-plan id are no
longer stored in `internal_event.trace_id`. The canonical event `trace_id` and
`correlation_id` are always `broadcast_task.created:{task_id}`.

For diagnostics that previously looked up `broadcast_task.created` by upstream
trace, use the safe trace lookup filter on the internal event list API:

```http
GET /api/admin/internal-events?event_type=broadcast_task.created&original_trace_hash={upstream_trace_or_hash}
```

`trace_hash` is accepted as an alias:

```http
GET /api/admin/internal-events?event_type=broadcast_task.created&trace_hash={upstream_trace_or_hash}
```

The service hashes non-hash input before querying
`payload.broadcast_task.original_trace_hash`, so callers may pass a non-PII
plan code or a precomputed 16-character hash. If the raw upstream trace happens
to look like a 16-character hexadecimal hash, the service tries both candidates:
the supplied value as a precomputed hash and `sha256(supplied)[:16]` as the raw
trace hash. The API does not return or persist the raw upstream trace, raw
`external_userid`, mobile number, openid, unionid, webhook URL, token, or
message body.

The list and diagnostics APIs also redact filter echoes. `trace_hash` and
`original_trace_hash` request parameters are accepted as raw lookup input, but
`response.filters` returns a safe `trace_ref:{sha256(input)[:16]}` view instead
of the raw query value. This keeps repository lookup behavior compatible while
preventing raw upstream trace, plan id, `external_userid`, mobile, openid, or
unionid values from being reflected back in API responses.

`payload_summary_json` is the admin-visible summary:

- `task_id`
- `task_type`
- `send_channel`
- `source`
- `campaign_code`
- `ops_plan_id`: `ops_plan_ref:{sha256[:16]}` when an ops plan is present.
  This field name is retained for compatibility, but the value is never raw.
- `ops_plan_ref`: same safe reference.
- `ops_plan_hash`: the first 16 hex characters of a SHA-256 hash.
- `ops_plan_present`: boolean indicator only.
- `target_count`
- `status`
- `scheduled`

It must not include raw upstream plan ids, raw trace ids, raw source ids,
customer lists, mobile numbers, raw `external_userid`, openid, unionid, full
message bodies, full prompts, full strategy text, tokens, secrets, webhook URLs,
or external receiver URLs. The list and detail APIs expose this summary, so the
summary follows the same redaction rules as stored payloads.

## Feature Flag

Production default is off:

```bash
AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED=0
```

Emit requires all of:

- `AICRM_INTERNAL_EVENTS_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES` explicitly contains
  `broadcast_task.created`

`broadcast_task.created` uses the stricter explicit allowlist gate. An empty
`AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES` value skips emits with:

```text
broadcast_task_event_type_not_explicitly_allowed
```

Diagnostics expose:

- `broadcast_task_internal_events_enabled`

## Write Paths

Current emit surfaces are the existing broadcast job creation paths:

- `aicrm_next/extensions/growth/cloud_orchestrator/application.py`
  - `ApproveCloudPlanRecipientCommand.execute`
- `aicrm_next/automation/automation_engine/group_ops/action_dispatcher.py`
  - private message broadcast job enqueue
- `aicrm_next/channels/integration_gateway/wecom_group_adapter.py`
  - group message broadcast job enqueue
- AI Audience outbound planner / external_effect_job
  - automation task-plan broadcast job enqueue

Each path creates or reuses the broadcast job first, then calls `safe_emit`.
Internal event failures are returned as event diagnostics or logged, but they
must not fail the task creation path.

## Consumers

New `broadcast_task.created` events fan out to four consumers:

- `broadcast_queue_projection_consumer`
  - Current behavior: succeeded/no-op.
  - Result: `broadcast_queue_projection=broadcast_task_created_recorded`.
- `push_center_link_consumer`
  - Current behavior: succeeded/no-op with `push_center_link=shadow_only`.
  - Does not send or push anything externally.
- `broadcast_task_ai_assist_notify_consumer`
  - Current behavior: skipped with
    `broadcast_task_ai_assist_notify_not_configured`.
  - Uses a broadcast-task-specific name to avoid generic consumer-name
    allowlist risk.
- `audit_projection_consumer`
  - Current behavior: succeeded/no-op.
  - Result: `audit_projection=broadcast_task_created_recorded`.

For compatibility with older pending runs, the registry also accepts the legacy
consumer name `ai_assist_notify_consumer` for `broadcast_task.created`. This is a
dispatch-only handler alias and returns skipped with:

```text
broadcast_task_legacy_ai_assist_notify_not_configured
```

It is not part of the fan-out list for newly emitted events, so new
`broadcast_task.created` events create exactly the four consumers above.

No consumer performs real WeCom group send, WeCom private send, Feishu,
webhook, payment query, refund, or External Effect dispatch.

## Worker Pair Allowlist

Do not add `broadcast_task.created:*` pairs during the initial shadow rollout.
Current payment-only auto-execute should remain pair-aware:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS=payment.succeeded:order_projection_consumer,payment.succeeded:customer_business_summary_consumer,payment.succeeded:dnd_policy_consumer,payment.succeeded:ai_assist_notify_consumer,payment.succeeded:automation_payment_consumer
```

With that configuration, `broadcast_task.created` can emit and create pending
consumer runs, but worker preview/run-due must return zero effective candidates
for broadcast task consumers. Manual single-consumer execution remains available
for approved gray checks.

## Production Verification

### Q0: Flag Off

1. Set `AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED=0`.
2. Keep `broadcast_task.created` out of allowed event types.
3. Create a safe test broadcast task.
4. Confirm task creation succeeds.
5. Confirm no `broadcast_task.created` event or consumer runs were created.
6. Confirm no external effect attempts or real sends occurred.

### Q1: Shadow Emit

1. Set `AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED=1`.
2. Add `broadcast_task.created` to
   `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES`.
3. Keep `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS` payment-only.
4. Create a safe test broadcast task.
5. Confirm exactly one `broadcast_task.created` event.
6. Confirm four pending consumer runs.
7. Confirm payload summary redaction.
8. Confirm worker preview for broadcast task consumers is blocked by pair
   allowlist.

### Q2: Single-Consumer Gray

Use only the single-consumer endpoint:

```http
POST /api/admin/internal-events/{event_id}/consumers/broadcast_queue_projection_consumer/run
```

or:

```http
POST /api/admin/internal-events/{event_id}/consumers/audit_projection_consumer/run
```

Use `dry_run=true` first, then `dry_run=false` with `force=false`.

Expected result is succeeded/no-op with a clear result summary. No external
effect attempts, broadcast sends, WeCom calls, Feishu calls, webhook calls,
payment queries, or refunds should be created by this verification.

## Rollback

If verification fails:

```bash
AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED=0
```

Remove `broadcast_task.created` from
`AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES`.

Leave payment, questionnaire, customer tag, customer phone, AI Campaign, and
ops-plan settings unchanged unless incident evidence points to those families.
