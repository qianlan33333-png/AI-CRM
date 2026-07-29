# External Orders Repair Plan - 2026-06-22

Verdict: `REPAIR_PLAN_REQUIRED`

This plan follows:

- `docs/reports/evidence/external_orders_enablement_evidence_20260622.md`
- `docs/reports/evidence/external_orders_blocker_triage_20260622.md`
- `scripts/diagnose_external_orders_blockers.py`

It does not repair production data, execute internal event consumers, trigger
external effects, run migrations, modify deployment files, change environment
variables, or enable any new real external call.

## Current Evidence Baseline

External Orders evidence collected on 2026-06-22 proves the main order push path
is mostly linked:

- Order evidence exists for internal order id `156`.
- External Orders no-token, wrong-token, and valid-token paths were checked.
- Admin order visibility exists.
- Push Center projection `external_effect_job:96` is `sent`.
- External effect jobs `95` and `96` succeeded.
- External effect attempts `96` and `97` succeeded with recorded HTTP 200
  responses.
- `payment.succeeded` internal event `iev_***dff3` exists.
- Delivery and effect idempotency evidence exists.

Two blockers remain:

1. Internal event consumers are still pending.
2. Customer read model linkage is missing.

## Final Repair Decisions

| Blocker | Current status | Repair decision | Next PR required? | Can recollect External Orders evidence? |
| --- | --- | --- | --- | --- |
| Internal event consumers | `consumer_run_pending_due_to_config` | `run_due_not_executed` | Yes | No |
| Customer read model linkage | `linkage_missing` | `runtime_projection_repair_required` | Yes | No |

`can_claim_external_orders_90_plus=false`.

## 1. Internal Event Consumers Pending Decision

### Expected Consumers

For `payment.succeeded`, the expected consumers are registered in
`aicrm_next/platform/platform_foundation/internal_events/payment.py`:

| Consumer | Type / intent | Placeholder? | Expected terminal state |
| --- | --- | --- | --- |
| `order_projection_consumer` | payment order projection confirmation | No | `succeeded` |
| `webhook_order_paid_consumer` | plans/reuses `webhook.order_paid.push` external effect job | No | `succeeded` |
| `automation_payment_consumer` | passes payment event into automation runtime | No | `succeeded` or explained result |
| `customer_business_summary_consumer` | configured as no-op summary refresh placeholder behavior | No registered placeholder metadata; handler returns `skipped` | `skipped` |
| `dnd_policy_consumer` | configured as no-op DND placeholder behavior | No registered placeholder metadata; handler returns `skipped` | `skipped` |
| `ai_assist_notify_consumer` | configured as no-op AI assist placeholder behavior | No registered placeholder metadata; handler returns `skipped` | `skipped` |

### Actual Consumer Run Records

The production evidence showed all six consumer run records exist, so this is
not currently classified as `consumer_not_registered`.

| Consumer | Observed status | Attempt count | Error |
| --- | --- | --- | --- |
| `order_projection_consumer` | `pending` | `0` | none |
| `webhook_order_paid_consumer` | `pending` | `0` | none |
| `automation_payment_consumer` | `pending` | `0` | none |
| `customer_business_summary_consumer` | `pending` | `0` | none |
| `dnd_policy_consumer` | `pending` | `0` | none |
| `ai_assist_notify_consumer` | `pending` | `0` | none |

### Why `attempt_count=0`

The best current decision is `run_due_not_executed`.

Reasoning:

- All expected run rows exist.
- Every run is still `pending`.
- Every run has `attempt_count=0`.
- No run has a recorded error.
- The worker records attempts and increments `attempt_count` only when a
  consumer is dispatched.
- Therefore the due worker or single-consumer run path has not processed this
  event.

### Scheduler / Run-Due / Config

Existing code provides both preview and execution routes:

- `POST /api/admin/internal-events/run-due/preview`
- `POST /api/admin/internal-events/run-due`
- `POST /api/admin/internal-events/{event_id}/consumers/{consumer_name}/run`
- `POST /api/admin/internal-events/{event_id}/consumers/{consumer_name}/retry`
- `POST /api/admin/internal-events/{event_id}/consumers/{consumer_name}/skip`

Non-dry-run execution is guarded:

- requires internal token or admin action token depending on route
- requires internal events enabled
- requires auto-execute gate for due execution
- production auto-execute requires allowlisted event/consumer scope

The existing gray runbook already requires preview before execution and
batch-size-one operator execution for payment consumers.

### Disabled / Placeholder / Shadow / Not Applicable

Current decision:

- `consumer_disabled_by_config`: not proven by evidence
- `consumer_placeholder_only`: no, these six are registered payment consumers
- `shadow_only`: not enough current evidence to treat pending as safe
- `expected_not_applicable`: no
- `must_remain_blocking`: yes

Even the no-op style consumers must be executed to transition to `skipped`.
Until that happens, the evidence remains blocked.

### Repair Decision

`blocker_1_repair_decision=run_due_not_executed`

Required next PR / operator path:

1. Add or execute a payment consumer run-due evidence PR/runbook step, not a
   data repair.
2. In an approved operator window, preview the single event and execute
   `payment.succeeded` consumers one at a time.
3. Recollect evidence after each consumer reaches `succeeded` or `skipped`.
4. If execution is blocked by configuration, classify as
   `consumer_disabled_by_config` and propose a config/run-due repair PR.

## 2. Customer Read Model Linkage Decision

### Existing Identity And Linkage Evidence

The production evidence showed:

- Order `156` has a customer identifier on the payment order row, redacted in
  reports.
- Channel contact linkage exists.
- Channel id evidence exists and is redacted.
- `customer_list_index_next` lookup found no linked row.
- `customer_detail_snapshot_next` lookup was not confirmed as linked.

### Projection Design Check

The customer read model repository and backfill path are centered on customer
identity/contact sources such as contacts, external contact bindings, WeCom
identity/follow-user tables, tags, class status, and archived messages.

The available evidence proves an order-side identity and channel contact
linkage, but it does not prove that the current read model projection consumes
`wechat_pay_orders` or `automation_channel_contact` as a source sufficient to
create/update `customer_list_index_next`.

So the missing row is not safe to treat as `expected_not_applicable`.

### Existing Backfill / Projection Script

Existing script:

```bash
scripts/backfill_customer_read_model.py
```

Safety characteristics:

- dry-run by default
- write mode requires `--execute`
- write mode additionally requires `--allow-execute`
- explicit database URL is required for execution
- PostgreSQL execution is limited to local test/tmp database names by script
  safety checks

This is useful evidence that a backfill framework exists, but it is not yet an
approved production repair path for order-to-customer linkage.

### Repair Decision

`blocker_2_repair_decision=runtime_projection_repair_required`

Reasoning:

- Customer identity evidence exists.
- Channel contact linkage exists.
- Customer list/detail read model linkage is missing.
- The current projection/backfill shape does not prove this order identity is
  projected into `customer_list_index_next`.

Required next PR:

1. Add an order/customer linkage projection repair or explicit non-applicable
   classification.
2. Prefer a dry-run first: prove whether the redacted order customer identity
   can be resolved into a customer read model source row.
3. If source rows exist but target rows are missing, create an approved
   backfill runbook.
4. If source rows do not exist, add a runtime projection source or identity
   bridge before any production backfill.
5. Only after approved repair/reclassification should External Orders evidence
   be recollected.

## Machine-Readable Summary

```json
{
  "blocker_1_current_status": "consumer_run_pending_due_to_config",
  "blocker_1_repair_decision": "run_due_not_executed",
  "blocker_1_next_pr_required": true,
  "blocker_2_current_status": "linkage_missing",
  "blocker_2_repair_decision": "runtime_projection_repair_required",
  "blocker_2_next_pr_required": true,
  "can_recollect_external_orders_evidence": false,
  "can_claim_external_orders_90_plus": false,
  "required_operator_actions": [
    "preview and execute approved payment.succeeded consumers one at a time during an operator window",
    "confirm whether customer read-model linkage should exist for this order/customer identity"
  ],
  "required_runtime_repairs": [
    "repair customer read-model projection so payment order customer identity can link to customer list/detail"
  ],
  "required_backfill_or_projection_repairs": [
    "approved customer read-model projection/backfill repair path required before External Orders 90%+ recollection"
  ]
}
```

## Sensitive Data Redaction Evidence

This report contains only internal numeric ids, redacted event ids, and
classification strings. It does not contain:

- token
- `Authorization` header
- raw `external_userid`
- phone number
- `unionid` / `openid`
- full order number
- customer secret
- payment credential

## Next PR Recommendation

Recommended next PR:

```text
External Orders customer read-model linkage projection repair
```

Parallel operator action:

```text
Preview and execute payment.succeeded internal event consumers one at a time in an approved window.
```

Do not recollect External Orders 90%+ evidence until both blockers are resolved
or formally reclassified as non-blocking.

## Risk / Rollback

This PR is documentation and readonly diagnostic enhancement only. Rollback is
reverting this PR. There is no runtime rollback, data rollback, migration
rollback, deployment rollback, or environment rollback.
