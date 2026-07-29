# ops_plan.approved Internal Event

`ops_plan.approved` records the business fact that an operations plan was approved.
It does not mean automation schedules were refreshed, assistant notifications were
sent, or broadcast tasks were planned.

## Event Schema

- `event_type`: `ops_plan.approved`
- `aggregate_type`: `cloud_orchestrator_plan`
- `aggregate_id`: plan id
- `subject_type`: `ops_plan`
- `subject_id`: plan id
- `idempotency_key`: `ops_plan.approved:{aggregate_type}:{plan_id}:{approved_marker}`
- `source_module`: `cloud_orchestrator.application`
- `source_route`: `/api/admin/cloud-orchestrator/plans/{plan_id}/approve`

Payload contains only operational diagnostics:

- `plan_id` / `plan_code`
- `approval_status`, `review_status`, `run_status`
- `operator`
- `source`
- `target_count` / `audience_count`
- `campaign_code` when present
- `approved_at` when present
- `plan_type`, `stage`, `status`
- a redacted `plan_summary`

`payload_summary_json` is the only default admin-list surface and contains:

- `plan_id`
- `source`
- `operator`
- `target_count`
- `campaign_code`
- `approved=true`
- `plan_type`
- `stage`
- `status`

It must not include customer lists, mobile numbers, raw `external_userid`, full
prompts, full strategy text, tokens, or secrets.

## Feature Flag

Production default is off:

```bash
AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED=0
```

Emit requires all of:

- `AICRM_INTERNAL_EVENTS_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED=1`
- `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES` contains `ops_plan.approved`

Unlike the older generic event gate, `ops_plan.approved` requires an explicit
allowlist entry. An empty `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES` value skips
ops-plan emits with `ops_plan_event_type_not_explicitly_allowed`; it must not
fall back to allow-all for this event family.

Diagnostics exposes:

- `ops_plan_internal_events_enabled`

## Write Path

The write path is:

- `aicrm_next/extensions/growth/cloud_orchestrator/application.py`
- `ApproveCloudPlanCommand.execute`

The command approves the plan first, then calls `safe_emit`. Internal event
failures are logged and returned as `internal_event_status=failed`, but they must
not fail the approval path.

## Consumers

The event registers four consumers:

- `automation_schedule_refresh_consumer`
  - Current behavior: local succeeded/no-op.
  - Does not trigger real automation execution.
- `ops_plan_ai_assist_notify_consumer`
  - Current behavior: skipped with `ops_plan_ai_assist_notify_not_configured`.
  - Uses an ops-plan-specific name to avoid shared consumer-name auto-execute risk.
- `audit_projection_consumer`
  - Current behavior: succeeded with `audit_projection=ops_plan_approved_recorded`.
- `broadcast_task_planner_consumer`
  - Current behavior: skipped with `broadcast_task_planner_not_configured`.
  - Does not create or send real broadcast tasks.

For compatibility with older pending runs, the registry also accepts the legacy
consumer name `ai_assist_notify_consumer` for `ops_plan.approved`. This is a
dispatch-only handler alias and returns skipped with
`ops_plan_legacy_ai_assist_notify_not_configured`. It is not part of the
fan-out list for newly emitted events, so new `ops_plan.approved` events still
create exactly the four consumers above.

No consumer performs real WeCom, Feishu, webhook, payment, or refund calls.

## Idempotency Compatibility

New events use:

```text
ops_plan.approved:{aggregate_type}:{plan_id}:{approved_marker}
```

Older shadow runs may already exist with:

```text
ops_plan.approved:{aggregate_type}:{plan_id}
```

Before creating a new event, emit checks for the old key. If it exists, the
existing event is returned and no new event or consumer runs are created. This
keeps historical approvals idempotent without a schema migration or data
rewrite.

## Worker Pair Allowlist

Do not add `ops_plan.approved:*` pairs during initial production shadow rollout.
Current payment-only auto-execute should look like:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS=payment.succeeded:order_projection_consumer,payment.succeeded:customer_business_summary_consumer,payment.succeeded:dnd_policy_consumer,payment.succeeded:ai_assist_notify_consumer,payment.succeeded:automation_payment_consumer
```

With that configuration, `ops_plan.approved` may emit and create pending
consumer runs, but worker preview/run-due must return zero effective candidates
for ops-plan consumers. Manual single-consumer execution remains available for
approved gray checks.

## Production Verification

### Q0: Flag Off

1. Set `AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED=0`.
2. Keep `ops_plan.approved` out of allowed event types.
3. Approve a safe test ops plan.
4. Confirm approval succeeds.
5. Confirm no `ops_plan.approved` event or consumer runs were created.

### Q1: Shadow Emit

1. Set `AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED=1`.
2. Add `ops_plan.approved` to `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES`.
3. Keep `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS` payment-only.
4. Approve a safe test ops plan.
5. Confirm exactly one `ops_plan.approved` event.
6. Confirm four pending consumer runs.
7. Confirm worker preview for ops-plan consumers is blocked by pair allowlist.

### Q2: Single-Consumer Gray

Use only the single-consumer endpoint:

```http
POST /api/admin/internal-events/{event_id}/consumers/audit_projection_consumer/run
```

or:

```http
POST /api/admin/internal-events/{event_id}/consumers/automation_schedule_refresh_consumer/run
```

Use `dry_run=true` first, then `dry_run=false` with `force=false`.

Expected result is succeeded/no-op or skipped with a clear reason. No external
effect attempts, broadcast jobs, WeCom sends, Feishu sends, or webhooks should be
created by this verification.

## Rollback

If verification fails:

```bash
AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED=0
```

Remove `ops_plan.approved` from `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES`.
Leave payment, questionnaire, customer tag, customer phone, and AI campaign
settings unchanged unless the incident evidence points to those families.
