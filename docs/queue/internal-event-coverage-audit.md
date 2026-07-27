# Internal Event Coverage Audit

Baseline date: 2026-06-15

This document is the static full-repository coverage audit for business facts
that create queue work, side effects, external calls, or async tasks. It is an
audit artifact only. It does not authorize production configuration changes,
real External Effects execution, real webhook, WeCom, Feishu, broadcast send,
payment query, refund, or worker allowlist expansion.

## Scope And Method

Static scan command:

```bash
.venv/bin/python scripts/audit_internal_event_coverage.py > docs/reports/evidence/internal_event_coverage_audit.json
```

Scan roots:

- `aicrm_next/**`
- `tests/**`
- `docs/queue/**`
- `scripts/**`
- `migrations/**`

Generated JSON summary:

- `static_only=true`
- `production_accessed=false`
- scanned files: `1038`
- keyword hits: `30417`
- safe emit calls: `14`
- consumer registrations: `51`
- External Effect call sites: `142`
- direct HTTP call sites: `7`
- legacy runtime marker calls: `13`
- heuristic candidate gaps: `1537`

The JSON file is intentionally noisy. `candidate_gaps` are static hints for
human review; severity below is the reviewed result.

## Coverage Status Legend

- `covered_by_internal_event`: business fact is represented by an Internal Event
  and has consumer fan-out.
- `shadow_emit_only`: event emits and creates runs, but worker auto-execute is
  blocked by pair allowlist.
- `event_missing`: business fact or lifecycle state has no Internal Event.
- `consumer_missing`: event exists but no consumer fan-out was found.
- `legacy_direct_only`: side effect still has a direct or legacy path.
- `external_effect_only`: side effect is represented by External Effect Queue,
  not by a canonical Internal Event fact.
- `intentionally_out_of_scope`: read-only, diagnostic, import/export, local
  projection, or explicitly blocked fixture path.
- `needs_product_decision`: event semantics are plausible but need product and
  operations ownership before implementation.

Gap severity:

- `P0-blocker`: core business fact is still not represented in the event queue.
- `P1`: side effect or high-risk lifecycle still depends on a direct path, while
  the main business fact is covered or intentionally separate.
- `P2`: observability, marker, guard, diagnostics, or event-lifecycle gap.
- `P3`: documentation or test gap.

## P0-2 Coverage Matrix

| module/path | function/route/command | business fact | existing event_type | feature flag | idempotency key | consumers | side effects | current coverage status | legacy path marker exists | P0-2 included? | gap severity | recommended action |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py` | `_emit_payment_succeeded_internal_event` | WeChat payment notify accepted as paid | `payment.succeeded` | `AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED` | `payment.succeeded:{out_trade_no}` | `order_projection_consumer`, `webhook_order_paid_consumer`, `automation_payment_consumer`, `customer_business_summary_consumer`, `dnd_policy_consumer`, `ai_assist_notify_consumer` | webhook planning, automation bridge, summaries | `covered_by_internal_event` | `old_payment_direct_automation_bridge` | Yes | None | Keep payment-only worker pairs; collect natural automation execute evidence. |
| `aicrm_next/extensions/forms/questionnaire/h5_write.py` | H5 submit command | Questionnaire submission persisted | `questionnaire.submitted` | `AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED` | `questionnaire.submitted:{submission_id}` | `questionnaire_projection_consumer`, `questionnaire_webhook_consumer`, `questionnaire_tag_consumer`, `automation_questionnaire_consumer`, `customer_summary_consumer` | queue-only webhook planning, tag/automation planning | `shadow_emit_only` | historical retry logs only | Yes | None | Keep non-payment consumers out of worker allowlist until approved. |
| `aicrm_next/crm/customer_tags/live_mutation.py` | live tag mutation command | Customer tagged | `customer.tagged` | `AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED` | `customer.tagged:{command_id_or_idempotency}` | `tag_external_effect_shadow_consumer`, `tag_summary_consumer`, `ai_assist_notify_consumer` | WeCom tag plan only | `shadow_emit_only` | covered by customer tag side-effect plan; no separate marker required for this covered path | Yes | None | Keep raw external_userid redaction tests. |
| `aicrm_next/crm/customer_tags/live_mutation.py` | live tag mutation command | Customer untagged | `customer.untagged` | `AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED` | `customer.untagged:{command_id_or_idempotency}` | `tag_external_effect_shadow_consumer`, `tag_summary_consumer`, `ai_assist_notify_consumer` | WeCom tag plan only | `shadow_emit_only` | covered by customer tag side-effect plan; no separate marker required for this covered path | Yes | None | Keep pair-aware worker allowlist. |
| `aicrm_next/crm/identity_contact/application.py` | bind mobile to external contact | Customer phone/mobile bound | `customer.phone_bound` | `AICRM_INTERNAL_EVENTS_CUSTOMER_IDENTITY_ENABLED` | `customer.phone_bound:{stable_identity_key}:{mobile_hash}` | `customer_identity_projection_consumer`, `customer_summary_consumer`, `automation_phone_bound_consumer`, `customer_identity_ai_assist_notify_consumer` | automation and AI-assist planning only | `shadow_emit_only` | Not needed | Yes | None | Keep masked mobile summary checks. |
| `aicrm_next/extensions/growth/cloud_orchestrator/campaigns_write.py` | create campaign | AI campaign created | `ai_campaign.created` | `AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED` | `ai_campaign.created:{campaign_code}:created` | `ai_campaign_ai_assist_notify_consumer`, `campaign_summary_consumer`, `broadcast_task_planner_consumer`, `audit_projection_consumer` | AI-assist/broadcast planning only | `shadow_emit_only` | Not needed | Yes | None | Keep real send blocked. |
| `aicrm_next/extensions/growth/cloud_orchestrator/campaigns_write.py` | approve campaign | AI campaign approved | `ai_campaign.approved` | `AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED` | `ai_campaign.approved:{campaign_code}:{approved_marker}` | same AI campaign fan-out | AI-assist/broadcast planning only | `shadow_emit_only` | Not needed | Yes | None | No P0 gap. |
| `aicrm_next/extensions/growth/cloud_orchestrator/campaigns_write.py` | start campaign | AI campaign started | `ai_campaign.started` | `AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED` | `ai_campaign.started:{campaign_code}:{started_marker}` | same AI campaign fan-out | creates side-effect plan for start | `shadow_emit_only` | Not needed | Yes | None | Start-group path should be product-reviewed before worker auto-execute. |
| `aicrm_next/extensions/growth/cloud_orchestrator/application.py` | approve ops plan | Ops plan approved | `ops_plan.approved` | `AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED` | `ops_plan.approved:{aggregate_type}:{plan_id}:{approved_marker}` | `automation_schedule_refresh_consumer`, `ops_plan_ai_assist_notify_consumer`, `audit_projection_consumer`, `broadcast_task_planner_consumer` | automation schedule and broadcast planning only | `shadow_emit_only` | Not needed | Yes | None | Keep legacy `ai_assist_notify_consumer` alias dispatch-only. |
| `aicrm_next/extensions/growth/cloud_orchestrator/application.py`; `aicrm_next/automation/automation_engine/group_ops/action_dispatcher.py`; `aicrm_next/channels/integration_gateway/wecom_group_adapter.py` | broadcast/group/private job creation | Broadcast task created | `broadcast_task.created` | `AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED` | `broadcast_task.created:{job_id_or_trace}` | `broadcast_queue_projection_consumer`, `push_center_link_consumer`, `broadcast_task_ai_assist_notify_consumer`, `audit_projection_consumer` | broadcast queue only; no real send | `shadow_emit_only` | `old_group_ops_queue_gateway_send` for gateway path | Yes | None | Keep broadcast send disabled; lifecycle events are separate. |
| `aicrm_next/crm/owner_migration/application.py` | scoped or legacy owner migration execute | Owner migration executed | `owner_migration.executed` | `AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED` | `owner_migration.executed:{result_id}` | `customer_owner_projection_consumer`, `customer_summary_mark_dirty_consumer`, `owner_migration_ai_assist_notify_consumer`, `webhook_owner_migration_consumer` | webhook/AI-assist planning only | `shadow_emit_only` | `old_owner_migration_legacy_execute_path` | Yes | None | Keep count semantics tests and no raw owner/customer identifiers. |

Conclusion for P0-2 rows: no new `P0-blocker` was found. The current operating
mode remains shadow emit plus pair allowlist blocking for all non-payment
families.

## Reviewed Gap Matrix

| module/path | function/route/command | business fact | existing event_type | feature flag | idempotency key | consumers | side effects | current coverage status | legacy path marker exists | P0-2 included? | gap severity | recommended action |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `aicrm_next/extensions/commerce/commerce/admin_refunds.py`; `aicrm_next/extensions/commerce/commerce/api.py`; `aicrm_next/extensions/commerce/commerce/wechat_shop_service.py` | `request_refund`, `request_wechat_admin_refund`, `create_wechat_shop_refund_request` | Refund requested / refund provider state changed | none; static strings include `payment.refund_requested` / `payment.refund.updated` in docs/tests | none | none | none | WeChat Pay / WeChat Shop refund provider call; payment side effect | `event_missing` and `legacy_direct_only` | `old_payment_refund_direct_request` | No | P1 | Add `payment.refund_requested` and `payment.refund.updated` slices before any refund automation or real execution. Keep refund disabled in production until then. |
| `aicrm_next/extensions/commerce/commerce/wechat_pay_client.py` | `query_order_by_out_trade_no`, `create_refund` | Payment provider query/refund operation | External Effect types exist: `payment.wechat.order.query`, `payment.wechat.refund.request`, `payment.wechat.refund.query` | External Effects config | External Effect idempotency where planned | External Effect worker when enabled | direct client can execute HTTP if caller invokes it | `external_effect_only` / adapter direct-call risk | refund marker exists at admin entry; query marker not found | No | P1 | Ensure all payment query/refund callers go through External Effect Queue or explicit guarded admin route; add diagnostics for direct client use. |
| `aicrm_next/extensions/commerce/commerce/wechat_shop_service.py`; `scripts/run_wechat_shop_order_sync.py` | `sync_wechat_shop_order(s)`, order sync script | WeChat Shop order synced / order state imported | none | none | sync-run records only | none | direct WeChat Shop client requests; DB upsert | `event_missing` / `needs_product_decision` | no | No | P2 | Decide whether order sync is operational import only or needs `payment.succeeded`/order-updated events. Add marker/diagnostics for real sync script before scheduled use. |
| `aicrm_next/channels/channel_entry/wecom_adapter.py` | `ProductionWeComAdapter` methods | WeCom contact/tag/contact-way/transfer API operation | none | `AICRM_NEXT_WECOM_REAL_CALLS_ENABLED` | none | none | direct WeCom HTTP client behind env gate | `legacy_direct_only` | no dedicated marker | No | P1 | Keep default guarded adapter. Before enabling, route real WeCom operations through External Effect Queue or add explicit runtime marker and diagnostics guard. |
| `aicrm_next/channels/integration_gateway/wecom_customer_group_client.py`; `aicrm_next/channels/integration_gateway/wecom_media_upload_client.py` | group/media WeCom clients | WeCom group/media API operation | none | mode/env guarded by callers | none | none | direct WeCom HTTP client | `legacy_direct_only` | group gateway has `old_group_ops_queue_gateway_send`; media upload no marker | No | P1 | Keep real group/media calls disabled. Add External Effect type or marker before real media/group upload gray. |
| retired customer automation webhook tombstones in `aicrm_next/automation/automation_engine/api.py` | retired customer webhook retry/retry-due routes | Customer automation webhook delivery requested/retried | n/a | route tombstone only | n/a | none | none; implementation module removed | `retired_tombstone` | covered by legacy customer webhook retry marker for admin path | No | retired | No event needed unless a future Push Center product flow reintroduces a new Next-native webhook capability. |
| retired automation Runtime V2 / program route families | `/api/automation-runtime/v2/*`, `/api/admin/automation-conversion/programs*`, old member/timer actions | old runtime admission / program task orchestration | n/a | n/a | n/a | none | none | `retired_tombstone_or_unregistered` | covered by retirement contract tests | No | retired | Do not reintroduce as an Internal Event gap. AI Audience refresh/source-poke/outbound now owns automation audience operations. |
| `aicrm_next/crm/customer_tags/admin_write.py` | admin tag catalog create/update/delete/sync | WeCom tag catalog mutation requested | none | local write guards only | side-effect plan IDs | none | side-effect plan only; no Internal Event | `event_missing` | no | No | P2 | P0-2 covers customer tag/untag, not tag catalog admin. Add `customer_tag_catalog.*` events only if downstream consumers are needed. |
| `aicrm_next/crm/sidebar_write/application.py` | sidebar profile update / tag / phone write helpers | customer profile/sidebar write planned | emits `customer.phone_bound` only for mobile binding path | `AICRM_INTERNAL_EVENTS_CUSTOMER_IDENTITY_ENABLED` for phone | phone idempotency only | phone-bound consumers only | side-effect plans for other profile/tag writes | `event_missing` for profile update; `covered_by_internal_event` for phone bind | no | Partially | P2 | Decide whether `customer.profile_updated` is in scope. Keep tag/phone raw identifiers redacted. |
| `aicrm_next/extensions/growth/cloud_orchestrator/media_upload.py` | media upload planner | media upload requested | none | media upload adapter gates | side-effect plan ID | none | media upload side-effect plan | `external_effect_only` / `needs_product_decision` | no | No | P2 | Add `media.upload.requested` only before real media upload gray. |
| `aicrm_next/platform/admin_jobs/application.py`; `aicrm_next/platform/admin_jobs/notification_settings.py` | deferred jobs run, webhook retry, Feishu hourly report, broadcast approve/cancel | admin runner or broadcast lifecycle action | none for runner/approve/cancel/report | admin route guards | audit IDs | none | External Effect planning for Feishu/webhook; direct broadcast status update | `event_missing` / `external_effect_only` | `old_admin_jobs_deferred_run`, `old_customer_webhook_delivery_retry`, `old_broadcast_jobs_feishu_hourly_report`, `old_broadcast_jobs_direct_approve_cancel` | No | P2 | Keep markers for 7-day observation; add lifecycle events only where consumers need them. |
| `aicrm_next/platform/admin_config/api.py`; `aicrm_next/platform/admin_config/application.py`; `aicrm_next/platform/admin_config/settings.py` | app settings / push capability saves | runtime config changed | none | n/a | audit/config write only | none | can affect External Effects real execution by app_settings/env precedence | `needs_product_decision` | no | No | P2 | Add diagnostics/guard for External Effects drift before P1. Consider `runtime_config.changed` audit event later. |
| `aicrm_next/platform/platform_foundation/external_effects/*`; app settings integration | diagnostics/execution mode | External Effects execution mode changed | none | External Effects env/app_settings | n/a | n/a | real execution can drift if app_settings overrides env | `needs_product_decision` | no | No | P1 | Add startup guard and single source of truth for `real_execution_enabled`, `allowed_effect_types`, and app_settings precedence. |

## Required Special Checks

1. Direct `requests.post` / HTTP calls not through `external_effect_job`:
   - Found 7 direct HTTP call sites, all in adapter/client layers:
     `aicrm_next/channels/channel_entry/wecom_adapter.py`, `aicrm_next/extensions/commerce/commerce/wechat_pay_client.py`,
     `aicrm_next/extensions/commerce/commerce/wechat_shop_client.py`,
     `aicrm_next/channels/integration_gateway/wecom_customer_group_client.py`, and
     `aicrm_next/channels/integration_gateway/wecom_media_upload_client.py`.
   - No scattered webhook `requests.post` was found in business write paths.
   - Risk remains P1 for payment/refund and WeCom clients if callers bypass
     External Effects or explicit guards.

2. WeCom / Feishu real call paths without External Effects gate:
   - Feishu broadcast notification uses `ExternalEffectService().plan_effect`.
   - WeCom group/private sends are blocked or queued by default.
   - Production WeCom adapter and media/group clients still contain direct HTTP
     clients; keep disabled and add explicit markers before real gray.

3. Business write paths without `safe_emit`:
   - No uncovered P0-2 core business fact was found.
   - Non-P0-2 candidates include refund request/update, automation program
     publish/task scheduling, admin config changes, media upload, and tag catalog
     admin mutations.

4. `side_effect_plan` creation without Internal Event:
   - Present in automation runtime, customer webhook planning, sidebar writes,
     media upload, tag catalog admin, and some campaign/run-due planners.
   - Most are intentionally blocked/planned, but they lack canonical event facts
     if later worker automation needs fan-out.

5. Queue/outbox task creation without event:
   - P0-2 broadcast task creation is covered by `broadcast_task.created`.
   - Legacy `domain_event_outbox` and automation/broadcast queues remain outside
     Internal Event except where P0-2 explicitly emits. These are P2/P1 depending
     on whether real execution is enabled.

6. Event type constants without write path:
   - Existing P0-2 event types have write paths and consumer fan-out.
   - Candidate future constants/names include refund, payment query, broadcast
     approved/cancelled, and questionnaire/admin audit-like names. Do not treat
     them as implemented event families.

7. Events emitted without consumer fan-out:
   - P0-2 emitted events have fan-out.
   - `customer.tag_mutation` appears as a dynamic helper argument in static scan;
     reviewed implementation emits `customer.tagged` / `customer.untagged`.

8. Consumer names reused across event types:
   - Reused names include `ai_assist_notify_consumer`,
     `audit_projection_consumer`, `broadcast_task_planner_consumer`,
     `customer_summary_consumer`, and `tag_external_effect_shadow_consumer`.
   - Pair-aware allowlist exists and must remain enabled. Current worker
     `allowed_event_consumers` must stay `payment.succeeded:*` only until
     explicit approval.

9. Payload/API response raw identifier risk:
   - P0-2 documents and tests cover redaction for customer tags, phone bound,
     broadcast task, and owner migration.
   - Future event candidates must not copy raw external_userid, mobile, openid,
     unionid, webhook URL, token, secret, owner userid, or full customer lists
     into `payload_json`, `payload_summary_json`, list API, or detail API.

10. External Effects app_settings/env drift:
   - Prior production verification observed `real_execution_enabled` drift.
   - This remains a P1 guard gap before P1 worker/External Effects gray rollout.

## Top Gaps

1. P1: Refund request/update is a real business fact and can trigger provider
   calls, but has no Internal Event family.
2. P1: Payment query/refund and WeCom adapter clients contain direct HTTP call
   sites; they must remain blocked or be routed through External Effects before
   real gray.
3. P1: External Effects execution-mode drift needs a startup/diagnostics guard
   and app_settings/env precedence settlement.
4. P2: Automation runtime and customer webhook planners create side-effect plans
   without canonical Internal Event facts.
5. P2: Broadcast lifecycle after creation, especially approve/cancel, is marked
   but not an event family.
6. P2: Admin config changes can alter runtime execution posture but are not
   represented as Internal Events.
7. P3: Static scan has many heuristic candidates; keep the audit script as a
   repeatable first pass, not a release gate by itself.

## Recommendation

No P0 blocker was found for the P0-2 closeout set.

Before P1:

1. Add External Effects anti-drift guard.
2. Keep non-payment event consumers out of worker allowlist.
3. Add refund event slice before any refund automation or real refund execution.
4. Add markers or event slices for automation runtime and media/group WeCom only
   when product approves those domains for auto-execute.
5. Re-run `scripts/audit_internal_event_coverage.py` after every new event
   family PR and compare `candidate_gaps` deltas.
