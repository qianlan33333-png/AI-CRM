# P0-2 Internal Event Queue Final Gate

Baseline date: 2026-06-15

This is the final gate report for P0-2 Internal Event Queue coverage. It covers:

- full production read-only verification,
- legacy/direct path deprecation marking for a 7-day observation window,
- full repo coverage audit for business facts that trigger queue work or side
  effects.

This report does not authorize real External Effects execution, webhooks,
WeCom, Feishu, broadcast send, payment query, refund, non-payment worker
auto-execute, or legacy path deletion.

## A. Full Production Verification

Production checks were read-only. No migration was created, no production
configuration was changed, no run-due execute was called, and no `force=true`
was used.

### Runtime

- `/health`: HTTP 200
- `X-AICRM-Route-Owner`: `ai_crm_next`
- runtime owner: `ai_crm_next`
- legacy runtime enabled: `false`
- production data ready: `true`
- release header observed:
  `d996e6cd4eb08744d0a2568056ab9261f4bab394-hxc-backend-refresh-hotfix`

### Internal Events Diagnostics

All P0-2 flags are enabled:

- `internal_events_enabled=true`
- `payment_internal_events_enabled=true`
- `questionnaire_internal_events_enabled=true`
- `customer_tags_internal_events_enabled=true`
- `customer_identity_internal_events_enabled=true`
- `ai_campaign_internal_events_enabled=true`
- `ops_plan_internal_events_enabled=true`
- `broadcast_task_internal_events_enabled=true`
- `owner_migration_internal_events_enabled=true`

`allowed_event_types` contains all P0-2 event families:

- `payment.succeeded`
- `questionnaire.submitted`
- `customer.tagged`
- `customer.untagged`
- `customer.phone_bound`
- `ai_campaign.created`
- `ai_campaign.approved`
- `ai_campaign.started`
- `ops_plan.approved`
- `broadcast_task.created`
- `owner_migration.executed`

`allowed_event_consumers` is payment-only:

- `payment.succeeded:order_projection_consumer`
- `payment.succeeded:customer_business_summary_consumer`
- `payment.succeeded:dnd_policy_consumer`
- `payment.succeeded:ai_assist_notify_consumer`
- `payment.succeeded:automation_payment_consumer`

Other diagnostics:

- `pair_allowlist_enabled=true`
- `config_warnings=[]`
- `failed_terminal_count=0`
- SQL stale lock check: `0`
- `blocked_by_pair_allowlist_count=89`
- `due_count=104`
- `real_external_call_executed=false`

### External Effects Diagnostics

External Effects remain disabled:

- `real_execution_enabled=false`
- `execution_mode=disabled`
- `allowed_effect_types=[]`
- `real_external_call_executed=false`
- current counts: `total=40`, `failed=0`

### Event Coverage Evidence

All P0-2 event types have production event rows and consumer run rows:

| event_type | event_count | latest event evidence | fan-out evidence |
|---|---:|---|---|
| `payment.succeeded` | 5 | latest `iev_7758d4630a734d37ba930319b53c90ce`, idempotency `payment.succeeded:WXP26061507575744B4CB0BE47A` | 6 consumer names present |
| `questionnaire.submitted` | 3 | latest `iev_1c52a2452fbd424582f9b32ff0846503`, aggregate `1385` | 5 consumer names present |
| `customer.tagged` | 3 | latest `iev_c8508601f2a546fc8593255029f2e509`, redacted external user summary | 3 consumer names present |
| `customer.untagged` | 3 | latest `iev_f5910f11e59a4430ab0cfe48fccf729b`, redacted external user summary | 3 consumer names present |
| `customer.phone_bound` | 3 | latest `iev_e67d3a0af09f41cfb97e1f4b67b4c25c`, masked mobile summary | 4 consumer names present |
| `ai_campaign.created` | 1 | `iev_ddd54cd1ff1d460195ef1a158ed38693` | 4 consumer names present |
| `ai_campaign.approved` | 1 | `iev_caecec84c08d4b12b6aca4fec2792254` | 4 consumer names present |
| `ai_campaign.started` | 1 | `iev_d23df01af6da4a928a5b1b4366839818` | 4 consumer names present |
| `ops_plan.approved` | 2 | latest `iev_974c65ba814f4b218042fe402fb07d2e` | 4 consumer names present |
| `broadcast_task.created` | 7 | latest `iev_c7cf8d861da0436e9fccbc1dbbd8d8b8`, trace `broadcast_task.created:2791` | 4 consumer names present |
| `owner_migration.executed` | 2 | latest `iev_594c41a15eab4d619b8dc7d82584c075`, count semantics `ok` | 4 consumer names present |

Consumer status check:

- No P0-2 event family has `failed` consumer runs.
- Non-payment families are not in `allowed_event_consumers`.
- Non-payment successes/skips present in production are from approved
  single-consumer gray checks, not run-due batch execution.

Run-due preview check:

- Explicit non-payment run-due preview without internal token returned HTTP 401
  `internal_token_required`.
- Response header kept `X-AICRM-Real-External-Call-Executed=false`.
- No run-due execute was called.

## B. Legacy Path Deprecation Marking

Existing legacy cleanup tables are reused. No migration is added. Runtime markers
are stored as `legacy_webhook_cleanup_audit` rows with these actions:

- `legacy_path_invoked`: a legacy/direct path was touched.
- `legacy_real_execution`: a legacy direct execution path was used. This blocks
  cleanup deletion during the 7-day observation window.

`/api/admin/legacy-webhook-cleanup/status` now returns per-entry:

```json
{
  "runtime_observation": {
    "window_days": 7,
    "legacy_path_invoked_count": 0,
    "legacy_real_execution_count": 0,
    "no_recent_real_execution": true
  }
}
```

It also returns aggregate `runtime_observation` totals for the filtered status
response.

### Marked Legacy / Direct Paths

| legacy_key | path | marker behavior | replacement / target |
|---|---|---|---|
| `automation_payment_consumer` | `platform_foundation.internal_events.payment` | retired consumer returns skipped; AI Audience source-poke handles audience refresh | `payment.succeeded:ai_audience_source_poke_consumer` |
| `old_questionnaire_sync_external_push` | questionnaire external push retry logs | records `legacy_path_invoked` for historical manual retry paths; H5 submit is queue-only | `questionnaire.submitted` + External Effect Queue |
| `old_external_push_outbox_worker` | `external_push.service.run_due_external_push_events` | records `legacy_path_invoked` | `payment.succeeded` + External Effect Queue |
| `old_external_push_delivery_retry` | external push send/test/retry admin paths | records `legacy_path_invoked` | External Effect Queue / Push Center |
| `old_admin_jobs_deferred_run` | `/api/admin/jobs/deferred-jobs/run` | records `legacy_path_invoked` | future Internal Event consumer or External Effect Queue |
| `old_customer_webhook_delivery_retry` | admin webhook retry disabled paths | records `legacy_path_invoked` via disabled payload | External Effect Queue / Push Center |
| `old_broadcast_jobs_feishu_hourly_report` | broadcast job Feishu validation/hourly report | records `legacy_path_invoked` | External Effect Queue, Feishu disabled until approved |
| `old_owner_migration_legacy_execute_path` | `OwnerMigrationService._run_legacy` | records `legacy_path_invoked`; still emits `owner_migration.executed` | scoped owner migration flow |
| `old_group_ops_queue_gateway_send` | retired group_ops queue gateway | no runtime marker remains; group_ops now plans `wecom.message.group.send` External Effect jobs | External Effect Queue / Push Center |
| `old_broadcast_jobs_direct_approve_cancel` | broadcast job approve/cancel control-plane routes | records `legacy_path_invoked` | future broadcast lifecycle event if needed |
| `old_payment_refund_direct_request` | refund request admin routes | records `legacy_path_invoked`; no behavior change | future payment refund event/effect slice |

The previous P0-1 legacy defaults remain in the registry and are still included
in cleanup status:

- `old_ai_assist_direct_send`
- `old_ai_assist_webhook_outbound`
- `old_ai_assist_campaign_run_due_direct`
- `old_group_ops_broadcast_job_send`
- `old_group_ops_webhook_outbound`
- `old_order_webhook_push`
- `old_external_direct_wecom_webhook_payment_feishu_openclaw`

### Observation Rule

Keep the 7-day observation period. Do not delete legacy paths in this PR. A
future deletion PR must require:

- `legacy_real_execution_count=0` for the prior 7 days,
- no unexpected `legacy_path_invoked` spikes,
- Internal Event or External Effect replacement verified,
- rollback plan limited to config or previous release rollback.

## C. Full Event-Coverage Audit

### Covered By Internal Event Queue

| Business fact / trigger | Current event | Coverage status |
|---|---|---|
| Payment paid / WeChat payment notify accepted | `payment.succeeded` | Covered |
| Questionnaire H5 submission persisted | `questionnaire.submitted` | Covered |
| Customer tag mark command accepted/planned | `customer.tagged` | Covered |
| Customer tag unmark command accepted/planned | `customer.untagged` | Covered |
| Customer mobile binding succeeds | `customer.phone_bound` | Covered |
| AI campaign created | `ai_campaign.created` | Covered |
| AI campaign approved | `ai_campaign.approved` | Covered |
| AI campaign started | `ai_campaign.started` | Covered |
| Ops plan approved | `ops_plan.approved` | Covered |
| Broadcast/group/private task created | `broadcast_task.created` | Covered |
| Owner migration executed | `owner_migration.executed` | Covered |

### Covered By External Effect Queue, Not Internal Event Queue

These are side-effect delivery actions rather than canonical business facts in
P0-2. They should remain blocked from real execution until P1/P2 approval.

| Path | Queue / guard | Current status |
|---|---|---|
| Order paid webhook push | External Effect Queue via `webhook.order_paid.push` | planned/queued only; real execution disabled |
| Questionnaire submission webhook push | External Effect Queue via `webhook.questionnaire_submission.push` | planned only; real execution disabled |
| Customer tag WeCom mark/unmark | External Effect Queue / side-effect plan | shadow/planned only |
| Admin webhook delivery retry | External Effect Queue via `webhook.generic.push` | disabled or planned only |
| Feishu broadcast hourly report | External Effect Queue via `feishu.webhook.notify` | planned only; real execution disabled |
| Group ops outbound loopback | External Effect Queue via `group_ops.message.loopback` | planned only |
| Media upload families | External Effect gates and integration adapter guards | real upload disabled unless separately approved |

### Legacy / Direct Paths Now Marked For Observation

| Area | Files / functions | Marker key |
|---|---|---|
| Payment legacy automation bridge | `aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py` | `old_payment_direct_automation_bridge` |
| Questionnaire external push retry logs | `aicrm_next/extensions/forms/questionnaire/external_push_logs.py` | `old_questionnaire_sync_external_push` |
| External push outbox worker | `aicrm_next/external_push/service.py` | `old_external_push_outbox_worker` |
| External push retry/test | `aicrm_next/external_push/service.py`, `aicrm_next/extensions/commerce/commerce/external_push_admin.py` | `old_external_push_delivery_retry` |
| Admin deferred jobs runner | `aicrm_next/admin_jobs/application.py` | `old_admin_jobs_deferred_run` |
| Admin webhook retry disabled path | `aicrm_next/admin_jobs/routes.py`, `application.py` | `old_customer_webhook_delivery_retry` |
| Broadcast Feishu report | `aicrm_next/admin_jobs/notification_settings.py` | `old_broadcast_jobs_feishu_hourly_report` |
| Owner migration legacy execute path | `aicrm_next/owner_migration/application.py` | `old_owner_migration_legacy_execute_path` |
| Group ops broadcast job queue gateway | retired; group_ops now uses `external_effect_job` | `old_group_ops_queue_gateway_send` |
| Broadcast approve/cancel control-plane | `aicrm_next/admin_jobs/application.py` | `old_broadcast_jobs_direct_approve_cancel` |
| Payment refund request | `aicrm_next/extensions/commerce/commerce/admin_refunds.py` | `old_payment_refund_direct_request` |

### Coverage Gaps / Future Event Candidates

These are intentionally not solved by P0-2 and should be separate PRs if they
need canonical event semantics.

| Candidate event family | Current path | Why it is a gap | Suggested next step |
|---|---|---|---|
| `payment.refund_requested` / `payment.refund.updated` | admin refund request and refund notify paths | Refund is a business fact and provider side effect, but not part of P0-2 Internal Event Queue | Add refund event slice before any refund automation |
| `external_push.delivery_requested` / `external_push.delivery_retried` | product/order external push admin and worker paths | Current behavior is External Effect Queue planning plus legacy delivery tables, without canonical Internal Event fact | Keep marker during P1; consider event slice if Push Center needs consumer fan-out |
| `broadcast_task.approved` / `broadcast_task.cancelled` | broadcast job approve/cancel routes | P0-2 covers created only; approve/cancel are direct control-plane facts | Add lifecycle events only if downstream automation/audit needs consumers |
| `admin_jobs.deferred_run_requested` | admin deferred job runner | Direct admin runner remains outside Internal Event Queue | Replace with explicit preview/dry-run event or External Effect command |
| `media.upload.requested` | cloud orchestrator / media library upload paths | Media upload has adapter guards but no canonical Internal Event fact | Keep External Effects disabled; add event only before real upload gray |
| `wecom.transfer_result.queried` | owner migration transfer-result query route | Query route can call WeCom result APIs; not part of owner migration executed event | Keep real query blocked unless separately approved |
| `group_ops.webhook.received` / action events | group ops webhook inbound/action dispatch | Group ops side-effect planning exists, but inbound webhook facts are not P0-2 Internal Events | Audit separately before enabling webhook-driven automation |

## Final Gate Checklist

Before P1:

- External Effects guard must prevent drift back to executable.
- `allowed_event_consumers` must remain payment-only unless a single pair is
  approved.
- A registered `automation_worker` and a short-lived `internal_worker` JWT should be available for full preview evidence.
- Payment automation needs a natural payment worker auto-execute proof.
- No legacy path deletion before 7 clean observation days.
- Any refund, media upload, Feishu, WeCom, broadcast send, or webhook real
  execution must be a separate gray rollout.

## Architecture Boundary

- Capability owner: `aicrm_next/platform_foundation/internal_events`,
  `platform_foundation/legacy_cleanup`, and the owning write modules listed in
  the matrices.
- Routes involved: `/health`, `/api/admin/internal-events/*`,
  `/api/admin/external-effects/diagnostics`,
  `/api/admin/legacy-webhook-cleanup/*`, and read-only production diagnostics.
- External calls: not authorized.
- Production data: production verification was read-only.
- Fixture risk: local tests are not production evidence.
- Rollback: remove the marker calls and keep existing deprecation registry
  behavior; for runtime config incidents, disable the affected event family and
  keep payment-only pair allowlist.
