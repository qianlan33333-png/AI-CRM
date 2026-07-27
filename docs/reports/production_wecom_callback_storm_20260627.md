# Production Incident Report: WeCom Callback Storm Saturated AI-CRM Web App

Date: 2026-06-27
Timezone: Asia/Shanghai
Production host: 150.158.82.186
Public domain: https://www.youcangogogo.com

## Executive Summary

At around 2026-06-27 11:06 CST, AI-CRM pages and the WeCom sidebar became
unresponsive. Nginx and PostgreSQL were healthy, but the single FastAPI/Uvicorn
application process on `127.0.0.1:5001` was saturated by a WeCom callback retry
storm on `/wecom/external-contact/callback`.

The immediate recovery action was to temporarily make nginx return `200 success`
for POST requests to:

- `/wecom/external-contact/callback`
- `/api/wecom/events`

The application service was then restarted to clear the full listen backlog.
After mitigation, `/health` and `/sidebar/bind-mobile` returned HTTP 200 and the
5001 listen queue returned to zero.

Current recheck on 2026-06-27 15:30-15:32 CST confirms the pages are recovered
but still protected by the emergency mitigation:

- `https://www.youcangogogo.com/health`: HTTP 200 in about 0.11s.
- `https://www.youcangogogo.com/sidebar/bind-mobile`: HTTP 200 in about 0.11s.
- `https://www.youcangogogo.com/admin/automation-conversion`: HTTP 302 in about 0.11s.
- `https://www.youcangogogo.com/api/sidebar/v2/workbench`: HTTP 400 in about
  0.11s when called without `external_userid`, which is a normal input error,
  not a platform outage.
- Invalid POST to `/wecom/external-contact/callback`: HTTP 200 body `success`,
  proving the nginx quick ACK is still active.
- `https://www.youcangogogo.com/admin/webhook-inbox`: HTTP 404, so the durable
  inbox operations page is not yet deployed in production.

Host-local recheck over SSH confirms only `127.0.0.1:5001` is listening for the
AI-CRM app. There is no isolated `127.0.0.1:5002` callback ingress runtime yet.

## Incident Timeline

| Time CST | Observation |
| --- | --- |
| 11:04 | `/sidebar/bind-mobile` and sidebar APIs still returned HTTP 200. |
| 11:05 | Callback traffic increased; 123 callback requests were processed as HTTP 200. |
| 11:06 | Callback volume jumped to 554/minute; 512 returned 499, and page requests began failing. |
| 11:08 | `/sidebar/bind-mobile` returned 504 from nginx. |
| 11:09 | `/admin/automation-conversion` returned 504 from nginx. |
| 11:10 | Callback traffic reached 890/minute; app was restarted once, but the storm continued. |
| 11:12-11:16 | Callback traffic stayed around 1000-1200/minute, almost all 499. |
| 11:16 | App listen backlog was full: `LISTEN 2049/2048` on `127.0.0.1:5001`. |
| 11:17 | Temporary nginx callback quick ACK applied, nginx reloaded, app restarted. |
| 11:17-11:18 | `/health` and `/sidebar/bind-mobile` returned HTTP 200; callback POSTs returned nginx-level `200 success`; app queue was `0/2048`. |
| 15:30 | Host-local recheck showed nginx and `openclaw-wecom-postgres.service` running, 5001 listening, 5002 absent, and quick ACK still present in `/etc/nginx/sites-enabled/youcangogogo.conf`. |

## Evidence

### Public and Local Health

Before mitigation:

- `https://www.youcangogogo.com/health` timed out.
- `http://150.158.82.186/health` timed out.
- Production local `http://127.0.0.1:5001/health` timed out.

After mitigation:

- `https://www.youcangogogo.com/health` returned HTTP 200 in about 0.16s.
- `https://www.youcangogogo.com/sidebar/bind-mobile` returned HTTP 200 in about 0.16s.
- Production local `http://127.0.0.1:5001/health` returned HTTP 200 in about 0.003s.

Latest recheck:

- `https://www.youcangogogo.com/health` returned HTTP 200 in about 0.11s.
- `https://www.youcangogogo.com/sidebar/bind-mobile` returned HTTP 200 in about 0.11s.
- `https://www.youcangogogo.com/admin/automation-conversion` returned HTTP 302 in about 0.11s.
- `https://www.youcangogogo.com/api/sidebar/v2/workbench` returned HTTP 400
  for a missing required `external_userid`, proving the route is reachable.

### Service and Resource Status

The failure was not caused by a down system service or exhausted host resource:

- `openclaw-wecom-postgres.service` was active.
- nginx was active.
- PostgreSQL was accepting connections.
- Disk usage was normal: `/dev/vda2` at 34%.
- Memory was available: about 1.2 GiB available.
- PostgreSQL did not show long-running application queries during the checks.

Latest host-local service state:

- nginx is active and was last reloaded at 2026-06-27 11:17:16 CST.
- `openclaw-wecom-postgres.service` is active since 2026-06-27 11:17:17 CST and
  is running `python app.py run`.
- `ss -ltnp` shows `127.0.0.1:5001` listening with backlog capacity 2048.
- No `127.0.0.1:5002` callback ingress listener is present.

### Nginx Symptoms

Nginx error log showed application upstream timeout:

- `upstream timed out (110: Connection timed out) while reading response header from upstream`
- Affected user-visible paths included:
  - `/sidebar/bind-mobile`
  - `/admin/automation-conversion`

Nginx access log showed the callback storm:

| Minute CST | Callback Count | Status Breakdown |
| --- | ---: | --- |
| 11:05 | 123 | 123 x 200 |
| 11:06 | 554 | 34 x 200, 8 x 400, 512 x 499 |
| 11:07 | 710 | 710 x 499 |
| 11:08 | 662 | 662 x 499 |
| 11:09 | 720 | 720 x 499 |
| 11:10 | 890 | 802 x 499, 88 x 502 |
| 11:11 | 672 | 82 x 200, 582 x 499, 8 x 502 |
| 11:12 | 1027 | 1027 x 499 |
| 11:13 | 1025 | 1025 x 499 |
| 11:14 | 1233 | 1233 x 499 |
| 11:15 | 1089 | 1089 x 499 |
| 11:16 | 1142 | 1142 x 499 |
| 11:17 | 955 | 514 x 200, 352 x 499, 88 x 502, 1 x 301 |
| 11:18 | 119 | 119 x 200 |

Latest log recheck showed the storm persisted after quick ACK and then tapered:

| Minute CST | Callback Count |
| --- | ---: |
| 11:14 | 1233 |
| 11:25 | 1004 |
| 11:31 | 1200 |
| 11:44 | 1076 |
| 11:59 | 6 |

The current 15:00 hour showed only 16 callback POSTs at the time of recheck,
including manual probe requests.

Top callback source IPs during the 11:00 hour included:

- `106.55.202.215`: 1657
- `106.55.201.217`: 1624
- `106.55.227.187`: 1441
- `159.75.144.151`: 1432
- `112.53.2.93`: 1423

### Application Listen Backlog

Before mitigation:

```text
LISTEN 2049 2048 127.0.0.1:5001 users:(("python",pid=2728780,fd=13))
```

After mitigation:

```text
LISTEN 0 2048 127.0.0.1:5001 users:(("python",pid=2737037,fd=13))
```

### Database Processing Rate

Database event logs showed the app could only persist/process a fraction of the
incoming callback rate:

| Minute CST | DB Event Logs |
| --- | ---: |
| 11:05 | 123 success |
| 11:06 | 128 success, 12 failed |
| 11:07 | 126 success |
| 11:08 | 123 success |
| 11:09 | 105 success |
| 11:10 | 113 success |
| 11:11 | 190 success |
| 11:12 | 138 success |
| 11:13 | 117 success |
| 11:14 | 149 success |
| 11:15 | 147 success |
| 11:16 | 165 success |
| 11:17 | 45 success before mitigation took effect |

In the recent 30-minute window, event logs showed:

- `success`: 1672
- `failed`: 12

After nginx quick ACK was applied, callback POSTs no longer reached the app, so
new callback events are intentionally not being inserted while the mitigation is
active.

## Immediate Mitigation Applied

File changed on production:

- `/etc/nginx/sites-enabled/youcangogogo.conf`

Backup created:

- `/etc/nginx/sites-enabled/youcangogogo.conf.bak-codex-callback-quick-ack-20260627T111716`

Temporary behavior:

```nginx
location = /wecom/external-contact/callback {
    default_type text/plain;
    if ($request_method = POST) {
        return 200 "success";
    }
    proxy_pass http://127.0.0.1:5001;
    ...
}

location = /api/wecom/events {
    default_type text/plain;
    if ($request_method = POST) {
        return 200 "success";
    }
    proxy_pass http://127.0.0.1:5001;
    ...
}
```

Commands executed:

- `nginx -t`
- `systemctl reload nginx`
- `systemctl restart openclaw-wecom-postgres.service`

This was an emergency availability mitigation, not the permanent fix.

## Direct Cause

The direct cause was a callback retry storm on
`/wecom/external-contact/callback`. The application process could not process
callbacks as fast as nginx and WeCom delivered/retried them. The saturated
application listener caused unrelated user-facing pages to queue behind callback
requests and time out.

## Root Design Issues

1. Callback handling is synchronous in the HTTP request path.

   `aicrm_next/channels/channel_entry/api.py` defines the callback route as async, but it
   directly calls synchronous application work:

   - decrypt callback body
   - insert/update `wecom_external_contact_event_logs`
   - sync external contact identity
   - resolve channel entry
   - upsert channel contact
   - emit internal event
   - plan external effect jobs
   - update processing status

2. Web traffic and external callback traffic share the same single app worker.

   `app.py run` starts `uvicorn.run("aicrm_next.main:app", host=..., port=...)`
   with the default single process. When callbacks saturate the app, admin pages
   and sidebar pages are starved.

3. There is no callback-specific backpressure boundary.

   The system currently relies on request-time processing, rather than a
   durable queue with fast ACK and independent worker capacity.

4. Nginx has no production callback protection.

   Before mitigation, callback routes proxied directly to `127.0.0.1:5001`
   without route-specific rate limiting, queue shedding, or an isolated upstream.

5. The deployment path did not install the new callback-isolation runtime.

   The permanent fix needs a separate 5002 callback ingress service and a
   callback inbox worker timer. The deploy workflow has now been updated to
   install/start those units after web health passes, while keeping nginx
   cutover manual and checker-gated. It also runs
   `scripts/ops/check_wecom_callback_deploy_smoke.py` after starting the callback
   runtime, so a deployment where `/admin/webhook-inbox` or the webhook inbox
   JSON APIs still return 404/5xx fails before anyone treats the release as
   ready for cutover.

6. The observable route contract is misleading.

   The nginx comments still said the callback path was a temporary fallback, but
   the route is currently owned by `aicrm_next.channels.channel_entry` in the Next
   architecture.

## Business Impact

User-visible impact:

- Sidebar and admin pages became unavailable or slow from approximately 11:06
  until 11:17 CST.

Data impact:

- Before mitigation, only part of the callback storm was persisted and processed.
- During mitigation, POST callbacks are acknowledged by nginx and do not reach
  the application, so channel-entry side effects for those callback events are
  temporarily not recorded.
- This tradeoff was chosen to restore admin and sidebar availability quickly.

External side-effect impact:

- The mitigation does not enable any new real WeCom, payment, OAuth, OpenClaw,
  or MCP outbound call.
- It suppresses inbound callback processing temporarily.

## Recommended Permanent Fix

Detailed acceptance audit:

- `docs/reports/wecom_callback_permanent_fix_acceptance_audit_20260627.md`

### P0: Replace Request-Time Callback Processing With Fast ACK + Durable Queue

For callback POSTs:

1. Verify signature and decrypt enough to build an idempotency key.
2. Persist the raw callback envelope or decrypted payload into a durable queue
   table with `received`, `processing`, `succeeded`, `failed_retryable`,
   `failed_terminal`, and `dead_letter` states.
3. Return ACK immediately.
4. Process channel entry, identity sync, internal events, welcome message queue,
   tags, and profile description in an independent worker.

Acceptance criteria:

- Callback route P95 latency under 200 ms under burst traffic.
- User pages remain available while replaying at least 1200 callbacks/minute.
- Duplicate callbacks collapse by event key.
- Failed processing can retry without requiring WeCom to keep retrying HTTP.

Implementation status in local worktree:

- Added `webhook_inbox` migration:
  - `migrations/versions/0054_webhook_inbox.py`
  - This is a merge migration over the current local heads
    `0050_channel_entry_effect_status_queued` and
    `0053_automation_agent_runtime_config`.
- Added generic inbox repository:
  - `aicrm_next/platform/platform_foundation/webhook_inbox/repository.py`
  - Supports duplicate collapse, due-row acquisition with
    `FOR UPDATE SKIP LOCKED`, explicit retry/terminal/dead-letter states, and
    queue metrics.
  - Persists `processing_summary_json` after worker success so a single inbox
    row can be traced to downstream processing records.
- Added generic inbox service and typed row/metric models:
  - `aicrm_next/platform/platform_foundation/webhook_inbox/service.py`
  - `aicrm_next/platform/platform_foundation/webhook_inbox/models.py`
- Added WeCom-specific ingress and worker:
  - `aicrm_next/channels/channel_entry/inbox.py`
  - Worker API covers `preview_due(...)`, `run_due(...)`, and
    `dispatch_one(inbox_id)` for targeted operator replay.
- Added isolated callback ingress runtime:
  - `aicrm_next/channels/channel_entry/ingress_app.py`
  - `scripts/run_wecom_callback_ingress.py`
  - `deploy/openclaw-wecom-callback-ingress.service`
  - `deploy/nginx-wecom-callback-ingress.conf.example`
  - `scripts/ops/check_wecom_callback_ingress_cutover.py`
  - `scripts/ops/check_wecom_callback_permanent_fix_readiness.py`
  - `scripts/ops/prepare_wecom_callback_ingress_cutover.py`
  - This runtime exposes only `/health`, `/wecom/external-contact/callback`,
    and `/api/wecom/events`, so callback traffic can be routed to `127.0.0.1:5002`
    without sharing the admin/sidebar web process on `127.0.0.1:5001`.
- Changed callback POST path:
  - `aicrm_next/channels/channel_entry/api.py`
  - POST now verifies/decrypts, writes inbox, and ACKs.
  - POST no longer calls `process_wecom_external_contact_event(...)` inline.
  - ingress DB failure returns 503 instead of fake ACK.
- Added worker entrypoint and deploy units:
  - `scripts/run_wecom_callback_inbox_worker.py`
  - `deploy/openclaw-wecom-callback-inbox-worker.service`
  - `deploy/openclaw-wecom-callback-inbox-worker.timer`
- Added backend operations APIs:
  - `GET /api/admin/webhook-inbox/metrics`
  - `GET /api/admin/webhook-inbox/items`
  - `GET /api/admin/webhook-inbox/{id}`
  - `POST /api/admin/webhook-inbox/{id}/dispatch`
  - `POST /api/admin/webhook-inbox/{id}/retry`
  - `POST /api/admin/webhook-inbox/{id}/skip`
  - `POST /api/admin/webhook-inbox/run-due`
  - `GET /api/admin/wecom/callback/reconciliation`
- Added admin operations page:
  - `GET /admin/webhook-inbox`
  - `aicrm_next/platform/platform_foundation/webhook_inbox/templates/admin_console/webhook_inbox.html`
  - The page shows queue metrics, filters, recent inbox rows, item detail,
    manual dispatch/replay, manual retry, manual skip, dry-run worker
    consumption, and WeCom callback reconciliation.
  - Item detail shows the processing chain:
    `webhook_inbox -> internal_event -> internal_event_consumer_run ->
    external_effect_job -> external_effect_attempt`.
- Added unified External Effect realtime wakeup:
  - `aicrm_next/platform/platform_foundation/external_effects/realtime.py`
  - Replaces the channel-entry private welcome-message wakeup path.
  - Requires `AICRM_EXTERNAL_EFFECT_REALTIME_ENABLED=1`,
    `AICRM_EXTERNAL_EFFECT_REALTIME_ALLOWED_TYPES`, the normal
    `AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES`, and adapter-specific execution gate
    such as `AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE=1`.
- Added focused tests:
  - `tests/test_wecom_callback_inbox.py`
  - `tests/test_wecom_callback_ingress_runtime.py`
  - `tests/test_webhook_inbox_repository.py`
  - `tests/test_webhook_inbox_admin_api.py`
  - `tests/test_external_effects_realtime.py`
- Added emergency-mode observability:
  - `scripts/ops/check_callback_quick_ack_state.py`
  - `docs/runbooks/wecom_callback_storm.md`

Verification:

```bash
.venv/bin/python -m pytest -q \
  tests/test_wecom_callback_inbox.py \
  tests/test_webhook_inbox_repository.py \
  tests/test_webhook_inbox_admin_api.py \
  tests/test_external_effects_realtime.py \
  tests/test_external_effects_wecom_welcome.py \
  tests/test_next_channel_entry_callback_owner.py \
  tests/test_channel_entry_next_retirement_contract.py \
  tests/test_next_channel_entry_orchestrator.py \
  tests/test_router_registry_contract.py
```

Result: 30 passed, 1 warning.

After adding the admin operations page, processing-chain drilldown, isolated
callback ingress runtime, nginx cutover template/checker, and
page/route/deploy contract coverage:

```bash
.venv/bin/python -m pytest -q \
  tests/test_external_effects_realtime.py \
  tests/test_wecom_callback_cutover_plan.py \
  tests/test_wecom_callback_external_effect_boundary.py \
  tests/test_wecom_callback_ingestion_evidence.py \
  tests/test_wecom_callback_processing_evidence.py \
  tests/test_wecom_callback_rollback_evidence.py \
  tests/test_wecom_callback_sample_generator.py \
  tests/test_wecom_callback_objective_coverage.py \
  tests/test_wecom_callback_pressure_probe.py \
  tests/test_webhook_inbox_migration_contract.py \
  tests/test_wecom_callback_permanent_fix_readiness.py \
  tests/test_wecom_callback_ingress_runtime.py \
  tests/test_external_effects_wecom_welcome.py \
  tests/test_webhook_inbox_repository.py \
  tests/test_webhook_inbox_admin_api.py \
  tests/test_wecom_callback_inbox.py \
  tests/test_next_channel_entry_callback_owner.py \
  tests/test_channel_entry_next_retirement_contract.py \
  tests/test_next_channel_entry_orchestrator.py \
  tests/test_router_registry_contract.py \
  tests/test_deploy_workflow_contract.py::test_wecom_callback_ingress_systemd_unit_is_deployable
```

Result: 121 passed, 1 warning.

This permanent fix has not yet been applied to production. Production is still
protected by the emergency nginx quick ACK described above.

### P0: Isolate Callback Runtime From Admin and Sidebar Runtime

Use separate process or upstream capacity for callback ingress and user-facing
pages.

Implemented locally:

- `aicrm_next.channels.channel_entry.ingress_app:app` is a callback-only FastAPI app.
- `scripts/run_wecom_callback_ingress.py` runs that app on `APP_PORT`, defaulting
  to 5002.
- `deploy/openclaw-wecom-callback-ingress.service` runs it as an independent
  long-lived systemd service on `127.0.0.1:5002`.
- `deploy/nginx-wecom-callback-ingress.conf.example` provides the callback
  upstream with route-specific `limit_req`, `limit_conn`, 429 overload status,
  and 1s connect / 3s send-read upstream timeouts.
- `scripts/ops/check_wecom_callback_ingress_cutover.py` checks whether quick ACK
  is still present, whether each callback `location` proxies to 5002, whether
  each callback `location` has short timeouts and route-level backpressure,
  whether the ingress health endpoint responds, and whether an invalid callback
  POST no longer receives nginx-level plain `success`.
- `scripts/ops/check_wecom_callback_permanent_fix_readiness.py` aggregates
  5001 health, 5002 health, quick ACK state, nginx cutover state,
  `webhook_inbox` metrics and health thresholds, callback systemd unit state, and optional 1200/min
  pressure evidence from `scripts/ops/probe_wecom_callback_pressure.py`, plus
  same-sample ingestion evidence from
  `scripts/ops/check_wecom_callback_ingestion_evidence.py`, same-sample worker
  processing evidence from `scripts/ops/check_wecom_callback_processing_evidence.py`,
  and rollback-drill evidence from `scripts/ops/check_wecom_callback_rollback_evidence.py`. It
  reports `ready_for_production_cutover` separately from
  `ready_for_production_completion`, so missing pressure, `webhook_inbox`
  ingestion, worker processing, callback-worker isolation, downstream-worker isolation,
  internal-event worker isolation, or rollback
  evidence cannot be mistaken for final completion; pressure evidence must show requested
  and observed rate >= 1200/min, ingestion evidence must find a recent
  `webhook_inbox` row for `sample_validation.idempotency_key`, processing
  evidence must show the same row was consumed successfully without business
  side effects, and `same_sample_evidence.ok=true` must prove pressure,
  ingestion, and processing evidence all share the exact same `idempotency_key`.
  Worker-isolation evidence must show a single callback ACK while the callback worker is stopped.
  Downstream-worker evidence must show callback ACK plus sampled page
  availability while the external push worker is stopped. Internal-event worker
  evidence must show callback ACK plus sampled page availability while the
  internal-event worker is stopped. All callback sample evidence files must
  include `sample_validation.ok=true` for the encrypted WeCom
  callback sample, and live `webhook_inbox` health must satisfy
  `due_count <= 100`, `failed_retryable_count = 0`, `dead_letter_count = 0`,
  and `oldest_received_age_seconds <= 300`. Rollback evidence must show the
  emergency quick ACK backup can be restored and the permanent 5002 cutover was
  re-applied after the drill.
- `scripts/ops/generate_wecom_callback_sample.py` creates a valid encrypted
  callback URL/body pair from the current callback env, so pressure probes and
  canaries can use the same signed sample without exposing the token or AES key.
  Its default `ChangeType=del_external_contact` proves ingress, ACK, dedupe, and
  inbox persistence without triggering channel-entry effects. Local tests also
  prove that this generated sample decrypts, ingests, and dispatches through the
  inbox worker to a succeeded row.
- `scripts/ops/check_wecom_callback_objective_coverage.py` maps the objective to
  local assets, test proofs, explicit objective requirement groups, and the
  final production readiness JSON, so local readiness cannot be confused with
  production completion.
- `scripts/ops/prepare_wecom_callback_ingress_cutover.py` now includes a
  worker-isolation canary command group: stop the callback worker timer/service,
  send one validated callback to 5002, save
  `/tmp/wecom-callback-worker-isolation.json`, and restore the worker.
- It also includes a downstream-worker isolation canary: stop
  `openclaw-external-push-worker.service` if present, send one validated
  callback with page samples, save
  `/tmp/wecom-callback-downstream-worker-isolation.json`, and restore the worker.
- It also includes an internal-event worker isolation canary: stop
  `openclaw-internal-event-worker.timer` and
  `openclaw-internal-event-worker.service`, send one validated callback with
  page samples, save
  `/tmp/wecom-callback-internal-event-worker-isolation.json`, and restore the worker.

Still not applied in production:

- The production service has not been installed or started.
- nginx still has the emergency quick ACK rule; it has not been switched to a
  `127.0.0.1:5002` callback upstream.

Acceptance criteria:

- Saturating callback ingress does not affect `/health`, `/sidebar/*`, or
  `/admin/*`.
- Runtime has separate metrics for callback ingress and web page traffic.

### P1: Add Route-Specific Backpressure

Implemented locally in the cutover template and checker:

- route-specific `limit_req` and `limit_conn`
- short upstream timeout for callback ACK path
- explicit 429 overload response policy
- `scripts/ops/check_wecom_callback_ingress_cutover.py` now rejects callback
  cutover configs that omit the backpressure snippets, and it rejects partial
  cutovers where only one callback route has been moved to 5002

Still pending:

- merge the backpressure snippet into the production nginx `http{}` and callback
  `location` scopes during the approved cutover
- alert when callback 429/499/502/504 exceeds threshold

This is a protection layer, not a replacement for the durable queue.

### P1: Make Callback Processing Idempotent and Replayable

The existing event key logic should become the durable replay boundary:

- Store all inbound callbacks before heavy processing.
- Deduplicate on event key.
- Provide admin replay for failed/dead-letter events.
- Track per-stage status for identity sync, channel entry, internal event, and
  external effect planning.

### P1: Add External Effect Realtime Wakeup

Implemented locally:

- `wake_external_effect_job(...)` in platform foundation.
- Configurable realtime allowlist and max concurrency.
- Welcome-message wakeup now uses the generic dispatcher.
- If realtime or the normal execution gate is not enabled, the job remains
  queued for the normal external effect worker instead of being consumed early.

Not enabled in production yet.

### P1: Add Production Runbook and Guardrail Checks

Added initial runbook/checker:

- `docs/runbooks/wecom_callback_storm.md`
- `scripts/ops/check_callback_quick_ack_state.py`
- `scripts/ops/check_wecom_callback_ingress_cutover.py`
- `scripts/ops/check_wecom_callback_permanent_fix_readiness.py`
- `scripts/ops/prepare_wecom_callback_ingress_cutover.py`
- `scripts/ops/probe_wecom_callback_pressure.py`

The checker reports:

- whether nginx quick ACK is still enabled
- whether recent app callback business processing appears suppressed
- whether callback POST still returns nginx-level `200 success`
- whether `wecom_external_contact_event_logs` has recent app-side inserts

Guardrail checks now have local scripts for cutover/readiness/pressure sampling.
Remaining production evidence:

- callback backlog depth and `webhook_inbox` health thresholds
- callback processing worker health and worker catch-up behavior
- app listen backlog
- route P95/P99 latency
- 499/502/504 spikes

### P1: Add Admin Operations API

Backend APIs are implemented locally for queue metrics, item listing, manual
retry, manual skip, dry-run/default run-due, and WeCom callback reconciliation.
The frontend `/admin/webhook-inbox` page is also implemented locally and wired
into the admin shell navigation.

Processing-chain drilldown:

- Implemented locally through `processing_summary_json` written by
  `WeComCallbackInboxWorker`.
- `GET /api/admin/webhook-inbox/{id}` returns `item` and `processing_chain`.
- The page renders `webhook_inbox`, `internal_event`,
  `internal_event_consumer_run`, `external_effect_job`, and
  `external_effect_attempt` nodes.
- Existing production callbacks acknowledged directly by the emergency nginx
  quick ACK cannot show a downstream chain because they did not enter the app or
  `webhook_inbox`.

## Temporary Mitigation Rollback

Do not roll back until the callback storm has stopped or a permanent callback
queue/isolation fix is deployed.

Rollback commands:

```bash
sudo cp /etc/nginx/sites-enabled/youcangogogo.conf.bak-codex-callback-quick-ack-20260627T111716 /etc/nginx/sites-enabled/youcangogogo.conf
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl restart openclaw-wecom-postgres.service
curl -sS http://127.0.0.1:5001/health
```

## Architecture Boundary

- Capability owner: `channel_entry` for WeCom callback routes; shared runtime
  for web availability.
- Routes involved:
  - `/wecom/external-contact/callback`
  - `/api/wecom/events`
  - `/health`
  - `/sidebar/bind-mobile`
  - `/admin/automation-conversion`
- Current owner:
  - callback routes: `aicrm_next.channels.channel_entry`
  - user pages: AI-CRM Next web app
- Production data: yes, production logs and read-only database diagnostics were
  used.
- Real external calls: no new outbound real external calls were enabled.
- Fixture/local-contract risk: none in the production diagnosis; evidence came
  from production logs, production health, and production DB.
- Checker needed: yes, add callback storm and app backlog checker.
- Rollback: restore nginx backup listed above, reload nginx, restart app after
  callback storm is controlled.
