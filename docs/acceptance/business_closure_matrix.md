# Business Closure 90% Acceptance Matrix

Date: 2026-06-22

## Purpose

This matrix translates the P0 architecture baseline into business acceptance
criteria for trial operations. It does not change runtime behavior, routes,
production deploy/systemd/nginx/env configuration, or real external-call gates.

Current technical baseline:

- Five architecture gates are wired into CI: route ownership, import/legacy
  boundary, external effects boundary, DB/session boundary, and background job
  contract.
- `docs/architecture/external_effects_registry.yml` has
  `temporary_allowlist: []`.
- `docs/architecture/db_access_boundary.yml` has `temporary_allowlist: []`.
- `aicrm_next/router_registry.py` owns router registration order.
- P0 closeout report PR `#1325` is documentation-only and should be merged as
  the official closeout record once GitHub review requirements are satisfied.

Non-goals for this matrix:

- No production deploy/systemd/nginx/env changes.
- No production migration execution.
- No real WeCom, Payment, OAuth, OpenClaw, MCP, or Feishu external call enabled.
- No P1 TypeScript frontend development.
- No claim that production gray-run evidence already exists unless a later
  acceptance PR records it.

## Capability Matrix

| Capability | Current status | 90%+ definition | 100% definition | Recommended next PRs |
| --- | --- | --- | --- | --- |
| Push Center / Group Ops | P0 gates protect external effects, DB access, and webhook/job route contracts; Push Center and broadcast job routes exist. | Operators can explain a send job from source to job/effect/attempt status, understand failure reason, and decide retry/cancel/manual action without DB access. | Real gray send, reconciliation, retry, cancel, and dead-letter handling are validated with approved receivers and production runbook evidence. | G1 push-center reconciliation payload; G2 group-ops gray-send acceptance. |
| Event / Approval / Task Loop | Internal event admin routes, run-due controls, reconciliation route, and background job contract exist. | An approved plan can be traced through internal event, consumer run, generated job/effect, and Push Center visibility with duplicate-safe behavior. | Operators can replay/retry failed consumers, see linked jobs/effects, and audit who approved, triggered, retried, or skipped every step. | E1 ops-plan-to-broadcast E2E acceptance; E2 event business explanation payload. |
| External Orders | Read-only external order APIs use registered `external_agent` client credentials and short-lived JWT; local WeChat Pay / WeChat Shop reads are Next-owned. | Client readiness, auth failures, read/query shape, idempotency expectations, and order/customer/channel correlation are verified before production enablement. | Approved external systems can create/query/update gray orders, duplicate calls are idempotent, and CRM/admin pages show reconciled order status. | O1 external-orders enablement package; O2 external-orders gray acceptance. |
| WeCom Real Auth / Callback | Admin WeCom auth and channel-entry callback routes are Next-owned and guarded; external calls remain approval-gated through integration gateway. | Operator auth readiness, callback signature behavior, duplicate event idempotency, and event/job visibility are validated without leaking secrets or raw IDs. | Real operator login, callback, contact/group permissions, token refresh, and gray send are validated in approved production-like conditions. | W1 WeCom auth operator readiness; W2 WeCom callback gray acceptance. |
| Core CRM Admin Operations | Channel, config, tags, orders, and direct-use business pages remain; broadcast, Push Center, and internal-event diagnostics are API/database-only. | Critical write flows show accurate save/error explanations and do not rely on stale assets or legacy fallback. | Admin operators use direct-use pages for configuration and actions; AI operations inspect jobs/events/auth state through authenticated APIs and production data. | A1 close or rebase #974; A2 channel auto-accept save/error refresh; A3 direct-use status language audit. |

## Push Center / Group Ops

### Business goal

Operators can answer: why this send exists, who it targets, whether it was sent,
where it failed, whether it can be retried, and whether manual action is needed.

### Current status

- Direct-use pages: `/admin/automation-conversion/group-ops/ui` and group-ops
  plan detail pages. Push Center and broadcast diagnostics are API/database-only.
- Relevant API / route owners:
  - `platform_foundation`: `/api/admin/push-center/sections`,
    `/api/admin/push-center/jobs`, `/api/admin/push-center/jobs/{job_id}`,
    `/api/admin/push-center/jobs/{job_id}/retry`,
    `/api/admin/push-center/jobs/{job_id}/cancel`.
  - `admin_jobs`: `/api/admin/broadcast-jobs`,
    `/api/admin/broadcast-jobs/{job_id}/approve`,
    `/api/admin/broadcast-jobs/{job_id}/cancel`.
  - `automation_engine`: group-ops plan, audience, execution, webhook, and
    run-due routes.
- Relevant pipelines: `broadcast_jobs`, `external_effect_job`,
  `external_effect_attempt`, `internal_event`, background job contract.
- P0 status: no direct business-layer `requests/httpx`; external effects must
  pass through integration gateway or platform external effects.

### 90%+ definition

- A single job detail payload links source plan/job, effect job, attempts,
  internal event or consumer run where available, effective status, retryability,
  and operator action.
- Success, partial success, blocked, retryable failure, dead-letter, and shadow
  failure are distinguishable.
- Retry/cancel actions return a business explanation, not only a technical
  status code.
- A dry-run or approved gray-run report can be attached to the PR evidence.

### 100% definition

- Approved gray receiver send proves real delivery, retry, cancel, and
  dead-letter visibility.
- Push Center status matches worker state, effect attempts, and broadcast job
  state across multiple providers/channels.
- Operators can run the acceptance checklist without database access.

### Production preconditions

- Approved real-send environment and receiver allowlist.
- Approved operator identity and audit policy.
- External effect worker/timer status verified before gray send.

### Acceptance cases

- Positive: queued job progresses to sent/succeeded with linked attempt and
  Push Center final status.
- Failure: adapter blocked, invalid target, provider failure, worker exception,
  and shadow failure each return a distinct explanation.
- Retry/compensation/reconciliation: retryable job can be retried once, cancelled
  jobs stay terminal, and shadow failure does not mark the business send failed
  when the main delivery succeeded.

### Operator-readable state

- `waiting_approval`: waiting for an approved operator action.
- `queued` / `pending`: accepted and not yet claimed by worker.
- `running` / `claimed`: worker is processing.
- `succeeded` / `sent`: business send completed.
- `failed`: worker or provider failure; inspect next action.
- `dead_lettered`: retry exhausted; manual handling required.
- `shadow_failed`: side-channel diagnostic failed; main business result may
  still be successful.

### Missing PRs

1. Add Push Center reconciliation payload or read-only diagnosis script.
2. Add group-ops gray-send acceptance package with default dry-run behavior.
3. Add operator-facing status wording tests for failure and retry states.

## Event / Approval / Task Loop

### Business goal

An approved operation plan can be traced from approval to internal event,
consumer run, generated job/effect, and Push Center visibility without requiring
engineer-only database inspection.

### Current status

- Direct-use pages: Cloud Orchestrator plan/campaign pages. Internal Event,
  Push Center, and broadcast diagnostics are API/database-only.
- Relevant API / route owners:
  - `platform_foundation`: `/api/admin/internal-events`,
    `/api/admin/internal-events/{event_id}`,
    `/api/admin/internal-events/{event_id}/reconciliation`,
    run/preview/retry/skip consumer routes.
  - `cloud_orchestrator`: plan, recipient, campaign approve/start/run-due
    routes.
  - `admin_jobs` and `platform_foundation`: broadcast job and Push Center
    projections.
- Relevant pipelines: `internal_event`, `internal_event_consumer_run`,
  background job contract, `broadcast_jobs`, `external_effect_job`.

### 90%+ definition

- Fixture or gray acceptance proves `ops_plan.approved` or equivalent approval
  event creates/reuses one internal event and one intended business job.
- Duplicate approval/event submissions do not duplicate jobs.
- Pending, skipped, failed, and succeeded consumer states have business-readable
  reasons.
- Event detail can link to generated job/effect where applicable.

### 100% definition

- Approval actor, source route, idempotency key, consumer decisions, generated
  jobs, retries, and final delivery state are auditable end to end.
- Replay/retry can be executed by an authorized operator with clear guardrails.

### Production preconditions

- Approved internal token for protected run/retry APIs.
- Worker/timer ownership and operating window documented.
- Audit retention policy for approval and consumer-run evidence.

### Acceptance cases

- Positive: plan approval emits event, consumer succeeds, job appears in Push
  Center.
- Failure: consumer missing configuration, target invalid, downstream job create
  failure, and worker exception return distinct reasons.
- Retry/compensation/reconciliation: retry only re-runs the failed consumer,
  duplicate event reuses existing idempotency key, skip records operator/audit
  context.

### Operator-readable state

- `pending`: accepted but waiting on consumer, worker, or required config.
- `skipped`: intentionally not executed with reason.
- `failed`: consumer or downstream job failed; retry may be available.
- `succeeded`: consumer completed and produced the expected effect/job or no-op.
- `reused`: duplicate event detected and linked to existing work.

### Missing PRs

1. Add ops-plan-to-broadcast E2E acceptance.
2. Add internal event business explanation fields:
   `derived_status`, `pending_reason`, `effect_job_status`, `retryable`,
   `operator_action_required`, `next_action_label`, `linked_push_center_job`.
3. Add focused tests for success, pending, failed, duplicate, retry, and skip.

## External Orders

### Business goal

External systems can safely query or submit order state, CRM can associate
orders to customer/channel/source, and operators can explain duplicate, failed,
or unauthorized calls.

### Current status

- Relevant docs: `docs/external_orders_api.md`.
- Relevant API / route owners:
  - `commerce`: `/api/external/orders`,
    `/api/external/orders/{order_no}`,
    `/api/admin/wechat-shop/orders/{order_id}/sync`,
    `/api/admin/wechat-shop/events`,
    `/api/admin/wechat-shop/sync-runs`.
  - `public_product`: `/api/h5/wechat-pay/*` payment entry/status routes.
  - `commerce`: `/api/wechat-pay/notify`, `/api/alipay/notify`,
    `/api/wechat-shop/notify`, refund notify.
- Relevant pipelines: local order read models, payment notify internal events,
  external effect jobs for order-paid push, background job contract.
- Current auth gate: external order APIs require an `external_agent` JWT with `external_integration` audience and `external_read` capability.

### 90%+ definition

- Enablement runbook verifies token missing, token absent, wrong token, and
  correct token behavior without leaking secrets.
- Query responses include stable order, payment status, provider, source, and
  correlation fields needed by CRM and external systems.
- Idempotency expectations for future write/update calls are documented and
  tested where write routes exist.
- Controlled 503/401/403 states are operator-readable.

### 100% definition

- Approved gray source can create/query/update orders with idempotency.
- Orders are visible in admin pages and linked to customer/channel/source.
- Order lifecycle emits internal events and downstream jobs/effects as expected.

### Production preconditions

- `external_agent` client ID/secret reference provisioned; RAUTH readiness returns `ok=true`.
- Approved gray source credentials and data redaction policy.
- Payment/provider callback configuration verified outside this PR.

### Acceptance cases

- Positive: correct token returns order list/detail and correlation fields.
- Failure: missing token, wrong token, unknown order, provider unavailable, and
  malformed filters return controlled errors.
- Retry/compensation/reconciliation: repeated call is idempotent, order status
  changes are traceable, and failed provider sync can be retried or explained.

### Operator-readable state

- `controlled_503`: feature/env not ready; configure token or provider.
- `unauthorized`: token missing or invalid.
- `not_found`: no local order matched the request.
- `pending` / `paid` / `closed` / `failed` / `refunded`: business payment state.
- `sync_failed`: provider/local sync failed; inspect sync run and retry policy.

### Missing PRs

1. Add external-orders enablement acceptance package and readiness script.
2. Add external-orders gray acceptance for approved source/system.
3. Add reconciliation evidence linking orders to customer/channel/source and
   internal event/job state.

## WeCom Real Auth / Callback

### Business goal

Operators can complete real WeCom auth/callback validation, understand permission
or token failures, and trace received contact/group events into inbound
event/job state.

### Current status

- Relevant pages/routes: `/login`, `/auth/wecom/start`,
  `/auth/wecom/callback`, `/wecom/external-contact/callback`,
  `/api/wecom/events`, `/api/admin/channels/runtime-diagnosis`,
  `/admin/channels`, `/admin/wecom-tags`.
- Relevant route owners:
  - `auth_wecom`: admin SSO start/callback.
  - `channel_entry`: external contact callback and runtime diagnosis.
  - `automation_engine`: channel code pages/APIs.
  - `customer_tags`: WeCom tag read/live mutation planning.
- Relevant pipelines: integration gateway WeCom client, external effect registry
  key `wecom.channel_entry.api`, internal events, background job contract.
- Current safety: real external calls remain approval-gated or
  staging-disabled; no raw token should be committed or logged.

### 90%+ definition

- Readiness script or runbook verifies corp/agent/redirect configuration without
  exposing secrets.
- Auth start, callback missing code, invalid state, and blocked token exchange
  have controlled responses.
- Callback verification, invalid signature, duplicate event, and enqueue/result
  visibility are covered by tests or gray acceptance.
- Operator identity and permission errors are understandable in admin-facing
  diagnostics.

### 100% definition

- Approved real operator can complete auth.
- Callback receives real gray event, passes signature verification, records
  inbound/internal event, and links to job/effect status.
- Token refresh/expiry and permission insufficiency are visible and actionable.

### Production preconditions

- Approved WeCom corp/agent/secret/redirect configuration.
- Approved test operator and event receiver scope.
- Redaction policy for tokens, external_userid, chat_id, and raw callback body.

### Acceptance cases

- Positive: auth start/callback readiness passes; valid callback creates or
  reuses event/job.
- Failure: invalid signature, invalid state, missing code, permission denied,
  expired token, and blocked adapter return controlled explanations.
- Retry/compensation/reconciliation: duplicate callback is idempotent; failed
  processing can be retried or dead-lettered with audit context.

### Operator-readable state

- `auth_blocked`: real token exchange not approved/enabled.
- `config_missing`: required corp/agent/redirect configuration absent.
- `permission_denied`: operator or app lacks required WeCom permission.
- `callback_invalid`: signature/payload failed validation; no job created.
- `event_recorded`: inbound/internal event accepted.
- `duplicate_reused`: duplicate callback reused existing event/job.

### Missing PRs

1. Add WeCom auth production readiness and operator acceptance runbook.
2. Add WeCom callback gray acceptance package.
3. Add diagnostic payload linking callback event to internal event/job/effect
   state without leaking raw identifiers.

## Core CRM Admin Operations

### Business goal

Admin users can configure the core CRM surfaces needed for trial operations and
understand save, validation, permission, queue, auth, and runtime status
failures without engineer intervention.

### Current status

- Relevant direct-use pages: `/admin/channels`, channel edit/new pages,
  `/admin/config/*`, `/admin/wecom-tags`, order/transaction admin pages, HXC
  dashboard, and media library. Queue and event diagnostics are API/database-only.
- Relevant route owners:
  - `automation_engine`: channel and group-ops admin APIs/pages.
  - `admin_config`: config pages.
  - `customer_tags`: WeCom tag read/plan routes.
  - `platform_foundation`: Push Center and internal event center.
  - `commerce`: order/payment admin surfaces.
  - `media_library`, `hxc_dashboard`, `admin_jobs`: supporting operator pages.
- Old draft PR `#974` is not part of the P0 closeout path. It should be closed
  or rebuilt from current `main` before channel admin UX work proceeds.

### 90%+ definition

- Critical save actions return concrete FastAPI `detail` or business error
  text, not generic failure.
- Stale static asset risk is handled for channel-code/auto-accept save flows.
- Admin diagnostics identify whether a feature is blocked by config, permission,
  worker, token, route ownership, or provider state.
- Read-only status pages are consistent with job/event/effect projections.

### 100% definition

- Operators can complete the trial-operation setup checklist, including channel
  config, auth readiness, send readiness, order readiness, and event/job
  troubleshooting, without developer DB inspection.
- All core admin actions have focused contract tests and visible error states.

### Production preconditions

- Approved admin/operator identity policy.
- Config ownership and secret redaction policy.
- Static asset cache strategy for admin JS changes.

### Acceptance cases

- Positive: channel auto-accept and related config saves persist and refresh
  visible state.
- Failure: validation error, stale asset, permission error, missing env, and
  provider blocked state are distinct.
- Retry/compensation/reconciliation: failed save can be retried safely; runtime
  diagnosis explains downstream state and does not trigger real external calls.

### Operator-readable state

- `saved`: config persisted and visible after reload.
- `validation_failed`: request rejected with field/detail explanation.
- `stale_asset`: browser may be using an outdated script; refresh/cache-busting
  needed.
- `config_missing`: required env/config absent.
- `provider_blocked`: real external adapter not approved/enabled.
- `diagnosis_ready`: route/runtime status can be inspected without mutation.

### Missing PRs

1. Close or rebuild #974 from current `main`.
2. Add channel auto-accept save refresh and error-detail fix without P1
   frontend refactor.
3. Add admin status-language audit across Push Center, internal events,
   channels, orders, and WeCom readiness.

## Recommended PR Order

1. Merge P0 closeout report PR `#1325` after GitHub review requirements are
   satisfied.
2. Add Push Center reconciliation payload or read-only diagnosis script.
3. Add group-ops gray-send acceptance with default dry-run behavior.
4. Add ops-plan-to-broadcast E2E acceptance.
5. Add internal event business explanation payload.
6. Add external-orders enablement acceptance package.
7. Add external-orders gray acceptance package.
8. Add WeCom auth operator readiness runbook and diagnostics.
9. Add WeCom callback gray acceptance.
10. Rebuild or close #974, then fix channel auto-accept save/error behavior from
    current `main`.
11. Start P1 TypeScript Frontend Foundation only after the 90%+ trial-operation
    acceptance evidence is recorded.

## Acceptance Package Links

The following dry-run acceptance packages are available for the next business
closure passes:

- `docs/acceptance/push_center_reconciliation_acceptance.md`
- `docs/acceptance/group_ops_gray_send_acceptance.md`
- `docs/acceptance/ops_plan_to_broadcast_e2e.md`
- `docs/acceptance/external_orders_enablement.md`
- `docs/acceptance/wecom_auth_operator_acceptance.md`
- `docs/acceptance/core_admin_operations_acceptance.md`
- `scripts/diagnose_business_closure_acceptance.py`

These packages are readiness and operator-evidence scaffolding. They do not
claim that real gray execution has already happened.

## Verification For This Matrix

Run these checks after changing this document:

```bash
git diff --check
bash scripts/ci/run_architecture_gates.sh
```

This document is acceptance planning only. Rollback is to revert the
documentation PR; runtime rollback is not required.
