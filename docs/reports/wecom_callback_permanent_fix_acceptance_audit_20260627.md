# WeCom Callback Permanent Fix Acceptance Audit

Date: 2026-06-27
Timezone: Asia/Shanghai
Scope: production callback-storm permanent fix readiness

## Summary

This audit maps the permanent-fix objective to current evidence. It separates:

- locally implemented and tested assets
- production emergency mitigation that is still active
- production cutover evidence that is still missing

Do not mark the permanent fix complete until the production-only items in this
file are verified on `150.158.82.186`.

## Latest Recheck

Current public and host-local probes on 2026-06-27 15:30-15:32 CST:

- `https://www.youcangogogo.com/health` returned HTTP 200 in about 0.11s.
- `https://www.youcangogogo.com/sidebar/bind-mobile` returned HTTP 200 in about 0.11s.
- `https://www.youcangogogo.com/admin/automation-conversion` returned HTTP 302 in about 0.11s.
- `https://www.youcangogogo.com/api/sidebar/v2/workbench` returned HTTP 400 in
  about 0.11s without `external_userid`, proving the route is reachable.
- Invalid callback POST to `/wecom/external-contact/callback` returned HTTP 200
  with body `success`, which means the emergency nginx quick ACK path is still
  active.
- `https://www.youcangogogo.com/admin/webhook-inbox` returned HTTP 404, so the
  new production operations page is not deployed yet.
- SSH host check showed nginx and `openclaw-wecom-postgres.service` running,
  `127.0.0.1:5001` listening, no `127.0.0.1:5002` listener, and the active
  nginx config still returning `200 "success"` for callback POSTs.

Follow-up public-only probes on 2026-06-27 16:38 CST:

- `https://www.youcangogogo.com/health` returned HTTP 200.
- `https://www.youcangogogo.com/sidebar/bind-mobile` returned HTTP 200.
- `https://www.youcangogogo.com/admin/automation-conversion` returned the login
  page with HTTP 200, so the route is reachable.
- `https://www.youcangogogo.com/admin/webhook-inbox` returned HTTP 404.
- `https://www.youcangogogo.com/api/admin/webhook-inbox/metrics?...` returned
  HTTP 404.
- `https://www.youcangogogo.com/api/admin/webhook-inbox/items?...` returned
  HTTP 404.
- `https://www.youcangogogo.com/api/admin/wecom/callback/reconciliation?...`
  returned HTTP 404.
- Invalid callback POST to `/wecom/external-contact/callback` returned HTTP 200
  with body `success`, so the public route still looks like emergency quick ACK.

This follow-up could not refresh host-local evidence because password SSH did
not authenticate. Treat the older host-local evidence as stale until the next
approved host login, but the public HTTP evidence still contradicts any claim
that the permanent fix is live in production.

Follow-up public-only probes on 2026-06-27 17:53-17:56 CST:

- `https://www.youcangogogo.com/health` returned HTTP 200.
- `https://www.youcangogogo.com/sidebar/bind-mobile` returned HTTP 200.
- `https://www.youcangogogo.com/admin/automation-conversion` returned the login
  page with HTTP 200, so the route is reachable.
- `https://www.youcangogogo.com/admin/webhook-inbox` returned HTTP 404.
- `https://www.youcangogogo.com/api/admin/webhook-inbox/metrics?...` returned
  HTTP 404.
- `https://www.youcangogogo.com/api/admin/webhook-inbox/items?...` returned
  HTTP 404.
- `https://www.youcangogogo.com/api/admin/webhook-inbox/0` returned HTTP 404.
- `https://www.youcangogogo.com/api/admin/wecom/callback/reconciliation?...`
  returned HTTP 404.
- Invalid callback POSTs to both `/wecom/external-contact/callback` and
  `/api/wecom/events` returned HTTP 200 with body `success`.

This proves the user-facing web layer is up, but the permanent cutover remains
unproven and the public callback routes still look like emergency quick ACK.

Follow-up public-only probes on 2026-06-27 18:25 CST:

- `https://www.youcangogogo.com/health` returned HTTP 200.
- `https://www.youcangogogo.com/sidebar/bind-mobile` returned HTTP 200.
- `https://www.youcangogogo.com/admin/automation-conversion` returned the login
  page with HTTP 200, so the route is reachable.
- `https://www.youcangogogo.com/admin/webhook-inbox` returned HTTP 404.
- `https://www.youcangogogo.com/api/admin/webhook-inbox/metrics?...` returned
  HTTP 404.
- `https://www.youcangogogo.com/api/admin/webhook-inbox/items?...` returned
  HTTP 404.
- `https://www.youcangogogo.com/api/admin/webhook-inbox/0` returned HTTP 404.
- `https://www.youcangogogo.com/api/admin/wecom/callback/reconciliation?...`
  returned HTTP 404.
- Invalid callback POSTs to both `/wecom/external-contact/callback` and
  `/api/wecom/events` returned HTTP 200 with body `success`.

This confirms the latest public state is still page availability plus emergency
quick ACK, not permanent 5001/5002 callback isolation.

Local objective coverage check on 2026-06-27 18:14 CST:

```bash
.venv/bin/python scripts/ops/check_wecom_callback_objective_coverage.py
```

Result: `local_contract_ready=true`, `production_completion_ready=false`,
and `ok=false` because production readiness JSON was not provided. This means
the repository has the local assets and tests for the objective, but the
production-only completion evidence is still missing.

Local approved-window command-plan check on 2026-06-27 18:14 CST:

```bash
.venv/bin/python scripts/ops/prepare_wecom_callback_ingress_cutover.py
```

Result: `ok=true`, `dry_run_only=true`, and `missing_assets=[]`. The generated
plan includes preflight, install/start, deploy smoke, nginx cutover, callback
sample generation, worker-isolation canaries, pressure probe, rollback,
reapply-cutover, rollback-drill evidence, and final readiness command groups.
It does not execute production changes; it is evidence that the cutover playbook
is locally complete enough to run inside an approved production window.

## Current Status

| Area | Status | Evidence |
| --- | --- | --- |
| Emergency page recovery | Complete | nginx quick ACK restored `/health` and sidebar/admin availability during the incident. |
| Generic `webhook_inbox` | Locally complete | `migrations/versions/0054_webhook_inbox.py`, repository/service/models, repository tests. |
| Fast ACK route | Locally complete | `aicrm_next/channels/channel_entry/api.py`, `tests/test_wecom_callback_inbox.py`. |
| WeCom callback worker | Locally complete | `aicrm_next/channels/channel_entry/inbox.py`, `scripts/run_wecom_callback_inbox_worker.py`, worker service/timer, worker tests including `dispatch_one(inbox_id)`. |
| Internal event boundary | Locally complete for channel entry | Worker calls existing `process_wecom_external_contact_event`, which emits `channel_entry.entered` outside the HTTP path. |
| External effect realtime wakeup | Locally complete, not enabled in production | `aicrm_next/platform_foundation/external_effects/realtime.py`, welcome/tag/profile gated wakeup tests, retryable adapter-exception handling, stale dispatching reclaim. |
| Runtime isolation and callback backpressure | Assets prepared, not cut over in production | `aicrm_next/channels/channel_entry/ingress_app.py`, 5002 systemd asset, deploy workflow install step, nginx example with `limit_req`/`limit_conn`/429, cutover checker. |
| Admin operations page | Locally complete | `/admin/webhook-inbox`, metrics/items/detail/dispatch/retry/skip/run-due/reconciliation tests. |
| Production permanent cutover | Not complete | Host check confirms no 5002 listener; nginx still has emergency quick ACK; no production pressure test yet. |

## Hard ACK Rules

| Requirement | Current Evidence | Status |
| --- | --- | --- |
| Signature verification failure returns 400 | `tests/test_callback_post_returns_400_when_verification_or_decrypt_fails` | Locally proven |
| Decrypt failure returns 400 | Same callback failure test covers decrypt/verify exception path | Locally proven |
| Successful enqueue returns ACK | `tests/test_callback_post_enqueues_and_acks_without_processing` and ingress runtime fast-ACK test | Locally proven |
| Duplicate callback returns ACK and collapses to one inbox row | `tests/test_ingest_wecom_callback_deduplicates_by_event_key` | Locally proven |
| DB enqueue failure returns 503 and does not fake ACK | `tests/test_callback_post_returns_503_when_inbox_write_fails` and ingress runtime 503 test | Locally proven |
| Worker failure does not affect HTTP ACK | HTTP route no longer calls `process_wecom_external_contact_event`; worker retry/dead-letter tests cover async failure path | Locally proven |

## Phase Audit

| Phase | Objective | Evidence | Status |
| --- | --- | --- | --- |
| Phase 0 | Observable emergency quick ACK state | `scripts/ops/check_callback_quick_ack_state.py`, route-level quick ACK detection tests, dual public callback POST probe tests, `docs/runbooks/wecom_callback_storm.md` | Locally complete; production script should be rerun before cutover |
| Phase 1 | Generic `webhook_inbox` schema/repository/service/metrics | `0054_webhook_inbox`, `aicrm_next/platform_foundation/webhook_inbox/*`, `tests/test_webhook_inbox_repository.py` | Locally complete |
| Phase 2 | Callback HTTP route only verifies/decrypts/enqueues/ACKs | `aicrm_next/channels/channel_entry/api.py`, `tests/test_wecom_callback_inbox.py` | Locally complete |
| Phase 3 | WeCom callback worker with retry/dead-letter and targeted dispatch | `aicrm_next/channels/channel_entry/inbox.py`, `scripts/run_wecom_callback_inbox_worker.py`, worker service/timer, `dispatch_one(inbox_id)` tests | Locally complete |
| Phase 4 | Business handling emits internal events outside HTTP path | Existing `process_channel_entry` emits `channel_entry.entered`; worker invokes it after claim | Locally complete for channel-entry flow |
| Phase 5 | Real outbound effects remain in `external_effect_job`; realtime wakeup is gated | `aicrm_next/platform_foundation/external_effects/realtime.py`, channel-entry welcome/tag/profile wakeups, adapter-exception retry handling, stale dispatching reclaim, realtime tests | Locally complete; production gates still disabled unless explicitly enabled |
| Phase 6 | Callback runtime isolated from admin/sidebar runtime with route-specific backpressure | 5002 ingress app, systemd asset, deploy workflow install/start checks, nginx template, cutover checker, ingress runtime tests | Assets ready; production cutover not complete |
| Phase 7 | Admin metrics, replay, dead-letter/detail, processing chain | `/admin/webhook-inbox`, `GET /api/admin/webhook-inbox/{id}`, `POST /api/admin/webhook-inbox/{id}/dispatch`, processing-chain tests | Locally complete |

## Production Cutover Checklist

These steps still require an explicit production deployment window.

Before the window, generate the dry-run command plan:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/prepare_wecom_callback_ingress_cutover.py
```

Expected: `ok=true`, no missing assets, and command groups for preflight,
systemd install with deploy smoke, nginx cutover, worker isolation canary,
downstream-worker isolation canary, internal-event worker isolation canary,
pressure probe, public-state evidence, rollback, reapply-cutover,
rollback-drill evidence, and final readiness.

1. Deploy code and run Alembic migration to `0054_webhook_inbox`.
2. Install and start `openclaw-wecom-callback-ingress.service`; the GitHub deploy workflow now copies and restarts this unit after web health passes.
3. Verify `curl -sSf http://127.0.0.1:5002/health`.
4. Install and enable `openclaw-wecom-callback-inbox-worker.timer`; the GitHub deploy workflow now copies and enables the worker service/timer but still does not modify nginx.
5. Run worker dry-run and then small execute batches.
6. Confirm `/admin/webhook-inbox` metrics show healthy backlog.
7. Replace nginx quick ACK with the 5002 callback upstream and include `limit_req`, `limit_conn`, and 429 overload status for callback routes.
8. Run `python scripts/ops/check_wecom_callback_ingress_cutover.py` after activating `/home/ubuntu/venvs/openclaw` and sourcing `/home/ubuntu/.openclaw-wecom-pg.env`.
9. Run `python scripts/ops/check_wecom_callback_permanent_fix_readiness.py` after activating `/home/ubuntu/venvs/openclaw` and sourcing `/home/ubuntu/.openclaw-wecom-pg.env`.
10. Confirm invalid callback POST no longer returns nginx-level plain `success`.
11. Confirm valid WeCom callbacks enqueue into `webhook_inbox` and ACK from app-level ingress by running `check_wecom_callback_ingestion_evidence.py` against the pressure sample idempotency key.
12. Run callback pressure test at or above 1200/min with `scripts/ops/probe_wecom_callback_pressure.py`.
13. Confirm `/health`, `/sidebar/bind-mobile`, and `/admin/automation-conversion` remain available during pressure.
14. Run the worker isolation canary from `prepare_wecom_callback_ingress_cutover.py`: stop the callback worker timer/service, send one valid callback with `--require-valid-callback-sample`, save `/tmp/wecom-callback-worker-isolation.json`, and restore the worker.
15. Run the downstream-worker isolation canary: stop `openclaw-external-push-worker.service` if present, send one valid callback with page samples, save `/tmp/wecom-callback-downstream-worker-isolation.json`, and restore the worker.
16. Run the internal-event worker isolation canary: stop `openclaw-internal-event-worker.timer` and `openclaw-internal-event-worker.service`, send one valid callback with page samples, save `/tmp/wecom-callback-internal-event-worker-isolation.json`, and restore the worker.
17. Keep rollback ready: restore nginx quick ACK backup and reload nginx.

## Verification Commands

Local verification currently passing:

```bash
.venv/bin/python -m pytest -q \
  tests/test_wecom_callback_cutover_plan.py \
  tests/test_wecom_callback_external_effect_boundary.py \
  tests/test_wecom_callback_ingestion_evidence.py \
  tests/test_wecom_callback_processing_evidence.py \
  tests/test_wecom_callback_rollback_evidence.py \
  tests/test_wecom_callback_sample_generator.py \
  tests/test_wecom_callback_objective_coverage.py \
  tests/test_wecom_callback_pressure_probe.py \
  tests/test_webhook_inbox_migration_contract.py \
  tests/test_wecom_callback_ingress_runtime.py \
  tests/test_wecom_callback_permanent_fix_readiness.py \
  tests/test_external_effects_realtime.py \
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

Latest local result: `121 passed, 1 warning`.

Latest focused local result at 2026-06-27 17:53 CST:

```bash
.venv/bin/python -m pytest -q \
  tests/test_wecom_callback_inbox.py \
  tests/test_webhook_inbox_repository.py \
  tests/test_deploy_workflow_contract.py \
  tests/test_wecom_callback_cutover_plan.py \
  tests/test_wecom_callback_objective_coverage.py \
  tests/test_wecom_callback_permanent_fix_readiness.py \
  tests/test_wecom_callback_deploy_smoke.py \
  tests/test_wecom_callback_public_state.py \
  tests/test_wecom_callback_processing_evidence.py \
  tests/test_webhook_inbox_admin_api.py
```

Result: `160 passed, 1 warning`.

Latest broader local result at 2026-06-27 17:56 CST:

```bash
.venv/bin/python -m pytest -q \
  tests/test_wecom_callback_inbox.py \
  tests/test_deploy_workflow_contract.py \
  tests/test_router_registry_contract.py \
  tests/test_wecom_callback_objective_coverage.py \
  tests/test_wecom_callback_public_state.py \
  tests/test_webhook_inbox_admin_api.py \
  tests/test_wecom_callback_deploy_smoke.py \
  tests/test_wecom_callback_external_effect_boundary.py \
  tests/test_channel_entry_next_retirement_contract.py \
  tests/test_wecom_callback_cutover_plan.py \
  tests/test_wecom_callback_ingress_runtime.py \
  tests/test_wecom_callback_sample_generator.py \
  tests/test_external_effects_realtime.py \
  tests/test_wecom_callback_ingestion_evidence.py \
  tests/test_external_effects_wecom_welcome.py \
  tests/test_wecom_callback_permanent_fix_readiness.py \
  tests/test_next_channel_entry_callback_owner.py \
  tests/test_wecom_callback_rollback_evidence.py \
  tests/test_next_channel_entry_orchestrator.py \
  tests/test_webhook_inbox_migration_contract.py \
  tests/test_wecom_callback_processing_evidence.py \
  tests/test_wecom_callback_pressure_probe.py \
  tests/test_external_effects_mvp.py \
  tests/test_webhook_inbox_repository.py
```

Result: `252 passed, 1 warning`.

Latest broader local result at 2026-06-27 18:04 CST, after adding dual-route
quick ACK probing to `check_callback_quick_ack_state.py`:

```bash
.venv/bin/python -m pytest -q \
  tests/test_wecom_callback_inbox.py \
  tests/test_deploy_workflow_contract.py \
  tests/test_router_registry_contract.py \
  tests/test_wecom_callback_objective_coverage.py \
  tests/test_wecom_callback_public_state.py \
  tests/test_webhook_inbox_admin_api.py \
  tests/test_wecom_callback_deploy_smoke.py \
  tests/test_wecom_callback_external_effect_boundary.py \
  tests/test_channel_entry_next_retirement_contract.py \
  tests/test_wecom_callback_cutover_plan.py \
  tests/test_wecom_callback_ingress_runtime.py \
  tests/test_wecom_callback_sample_generator.py \
  tests/test_external_effects_realtime.py \
  tests/test_wecom_callback_ingestion_evidence.py \
  tests/test_external_effects_wecom_welcome.py \
  tests/test_wecom_callback_permanent_fix_readiness.py \
  tests/test_next_channel_entry_callback_owner.py \
  tests/test_wecom_callback_rollback_evidence.py \
  tests/test_next_channel_entry_orchestrator.py \
  tests/test_webhook_inbox_migration_contract.py \
  tests/test_wecom_callback_processing_evidence.py \
  tests/test_wecom_callback_pressure_probe.py \
  tests/test_external_effects_mvp.py \
  tests/test_webhook_inbox_repository.py
```

Result: `254 passed, 1 warning`.

Latest broader local result at 2026-06-27 18:08 CST, after requiring deploy
smoke evidence to use distinct web and ingress base URLs:

```bash
.venv/bin/python -m pytest -q \
  tests/test_wecom_callback_inbox.py \
  tests/test_deploy_workflow_contract.py \
  tests/test_router_registry_contract.py \
  tests/test_wecom_callback_objective_coverage.py \
  tests/test_wecom_callback_public_state.py \
  tests/test_webhook_inbox_admin_api.py \
  tests/test_wecom_callback_deploy_smoke.py \
  tests/test_wecom_callback_external_effect_boundary.py \
  tests/test_channel_entry_next_retirement_contract.py \
  tests/test_wecom_callback_cutover_plan.py \
  tests/test_wecom_callback_ingress_runtime.py \
  tests/test_wecom_callback_sample_generator.py \
  tests/test_external_effects_realtime.py \
  tests/test_wecom_callback_ingestion_evidence.py \
  tests/test_external_effects_wecom_welcome.py \
  tests/test_wecom_callback_permanent_fix_readiness.py \
  tests/test_next_channel_entry_callback_owner.py \
  tests/test_wecom_callback_rollback_evidence.py \
  tests/test_next_channel_entry_orchestrator.py \
  tests/test_webhook_inbox_migration_contract.py \
  tests/test_wecom_callback_processing_evidence.py \
  tests/test_wecom_callback_pressure_probe.py \
  tests/test_external_effects_mvp.py \
  tests/test_webhook_inbox_repository.py
```

Result: `256 passed, 1 warning`.

Local schema head check:

```bash
.venv/bin/alembic heads
```

Expected: `0054_webhook_inbox (head)`.

Public production state check:

```bash
.venv/bin/python scripts/ops/check_wecom_callback_public_state.py
```

Expected before production cutover: `ok=false`,
`user_facing_available=true`, `admin_webhook_inbox_deployed=false`,
`invalid_callback_plain_success=true`. This is an HTTP-only gap detector; it
does not replace host-local readiness, DB schema checks, same-sample evidence,
or pressure evidence. After production cutover, this check only becomes
`ok=true` when the webhook-inbox routes return a deployed/auth signal rather
than 404/5xx and invalid callback probes for both
`/wecom/external-contact/callback` and `/api/wecom/events` are rejected with
app-level 4xx, not nginx-level plain `success` or upstream 5xx.

Cutover-template check:

```bash
.venv/bin/python scripts/ops/check_wecom_callback_ingress_cutover.py \
  --nginx-config deploy/nginx-wecom-callback-ingress.conf.example \
  --skip-health-probe \
  --skip-invalid-callback-probe
```

Expected: `ready_for_cutover=true`.

Aggregate production readiness check:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_wecom_callback_permanent_fix_readiness.py
```

Expected after production cutover: `ready_for_production_cutover=true`.
This includes the invalid callback probe through the cutover checker; an
invalid POST must not return nginx-level plain `success` and the public-state
probe should see app-level 4xx rather than upstream 5xx.
For production completion, pass the pressure-probe JSON with
`--pressure-evidence-file`, the same-sample `webhook_inbox` ingestion JSON with
`--ingestion-evidence-file`, the same-sample worker processing JSON with
`--processing-evidence-file`, and the worker-isolation JSON with
`--worker-isolation-evidence-file`, plus the downstream-worker isolation JSON
with `--downstream-worker-isolation-evidence-file`, the internal-event worker
isolation JSON with `--internal-event-worker-isolation-evidence-file`, and rollback-drill JSON with
`--rollback-evidence-file`, plus the public-state JSON with
`--public-state-evidence-file`, plus deploy-smoke JSON with
`--deploy-smoke-evidence-file`. Failed or missing evidence keeps the completion
claim unproven. Generate deploy-smoke evidence from:

```bash
python scripts/ops/check_wecom_callback_deploy_smoke.py \
  | tee /tmp/wecom-callback-deploy-smoke.json
```

The deploy-smoke JSON must prove web health, 5002 ingress health, webhook inbox
admin page/API/detail routes, and app-level invalid callback rejection for both
`/wecom/external-contact/callback` and `/api/wecom/events`. Plain HTTP 200
`success` on either callback route fails the completion gate. The smoke evidence
must also show distinct web and ingress base URLs, normally
`http://127.0.0.1:5001` and `http://127.0.0.1:5002`; using the same public URL
for both fails because it does not prove runtime isolation.

Latest public production deploy-smoke gap check at 2026-06-27 18:25 CST used
`https://www.youcangogogo.com` for both web and ingress and correctly returned
`ok=false` with `base_urls_distinct=false`. The same run also observed
`/admin/webhook-inbox` and the webhook inbox JSON APIs as 404, and invalid POST
probes to both callback URLs as HTTP 200 plain `success`. This confirms current
production is still emergency quick ACK plus page availability, not permanent
5001/5002 isolation.

`sample_validation.ok=true`, proving the callback sample decrypted with current
WeCom callback config before load was sent. The ingestion evidence must find a
recent `webhook_inbox` row for `sample_validation.idempotency_key` under
`provider=wecom` and `event_family=external_contact`. The processing evidence
must show that same row reached `status=succeeded`, has `finished_at`, and for
the default canary has `identity_sync_status=skipped` with no external effect
jobs. The checker also emits `same_sample_evidence`, which must be `ok=true`
and proves the pressure, ingestion, and processing JSON all reference the exact
same `idempotency_key`. The checker also evaluates live
`webhook_inbox` health: by default `due_count <= 100`, `failed_retryable_count = 0`,
`dead_letter_count = 0`, and `oldest_received_age_seconds <= 300`; `--skip-db`
keeps completion false because queue health is unproven. Rollback evidence must
show the emergency nginx backup can be restored, quick ACK can be re-enabled,
page health remains good, and the permanent cutover was re-applied after the
drill. Only after pressure, same-sample key validation, same-sample ingestion, same-sample processing, worker-isolation,
downstream-worker isolation, internal-event worker isolation, rollback evidence,
public state evidence, deploy-smoke evidence, and inbox health all pass should
`ready_for_production_completion=true` and `ok=true`.

Approved-window command plan:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/prepare_wecom_callback_ingress_cutover.py
```

Expected before the window: `ok=true` and `dry_run_only=true`. The generated
plan runs `check_wecom_callback_deploy_smoke.py` inside `install_and_start`,
saves `/tmp/wecom-callback-deploy-smoke.json`, then includes both
`/tmp/wecom-callback-public-state.json` and the deploy-smoke JSON in the final
readiness check through `--public-state-evidence-file` and
`--deploy-smoke-evidence-file`. Deploy smoke proves the local 5001/5002/admin
routes are deployed, including the 5002 ingress callback routes for both
`/wecom/external-contact/callback` and `/api/wecom/events` and the
webhook-inbox detail processing-chain route;
the final readiness command is emitted as a separate `final_readiness` group after
`rollback_drill_evidence`, so it only runs once rollback has been tested, the
`reapply_cutover_after_rollback` group has restored the permanent 5002 cutover,
and `/tmp/wecom-callback-rollback.json` has been captured and validated. The
cutover command persists the selected nginx backup path to
`/tmp/wecom-callback-cutover-backup-path`, and the rollback group reads that
file before restoring nginx so the rollback remains valid across separate shell
sessions.

Generate a valid callback canary sample after production or staging cutover:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/generate_wecom_callback_sample.py \
  --env-file /home/ubuntu/.openclaw-wecom-pg.env \
  --callback-base-url http://127.0.0.1:5002/wecom/external-contact/callback \
  --body-file /tmp/wecom-callback-sample.xml \
  --url-file /tmp/wecom-callback-sample.url \
  --metadata-file /tmp/wecom-callback-sample.json
```

Pressure probe after sample generation:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
set -o pipefail
python scripts/ops/probe_wecom_callback_pressure.py \
  --callback-url "$(cat /tmp/wecom-callback-sample.url)" \
  --callback-body-file /tmp/wecom-callback-sample.xml \
  --require-valid-callback-sample \
  --rate-per-minute 1200 \
  --duration-seconds 60 \
  | tee /tmp/wecom-callback-pressure.json
```

The generated canary defaults to `ChangeType=del_external_contact`, so it proves
valid encrypted callback ACK, dedupe, and `webhook_inbox` ingestion without
triggering identity sync, channel-entry projection, or external effect planning.
Local tests also prove that the generated encrypted sample decrypts, ingests,
and dispatches through the inbox worker to a succeeded row. Separate local tests
cover the full channel-entry worker path and external effect boundary.

Expected: `sample_validation.ok=true`, requested and observed callback rate >=
1200/min, callback P95 <= 200 ms, callback P99 <= 500 ms, `/health` P95 <= 100 ms,
`/sidebar/bind-mobile` P95 <= 300 ms, and no HTTP 5xx from sampled
admin/sidebar routes. The follow-up readiness check must also show accepted
same-sample key validation, accepted same-sample `webhook_inbox` ingestion evidence, accepted same-sample worker
processing evidence, accepted worker-isolation evidence, accepted downstream-worker isolation evidence, accepted internal-event worker isolation evidence, and healthy
`webhook_inbox` backlog thresholds. Save the JSON output, generate
`/tmp/wecom-callback-ingestion.json` with
`check_wecom_callback_ingestion_evidence.py --pressure-evidence-file`, preview
due rows with `python scripts/run_wecom_callback_inbox_worker.py --limit 20`,
then explicitly execute a small batch with
`AICRM_WECOM_CALLBACK_INBOX_WORKER_EXECUTE=1 python scripts/run_wecom_callback_inbox_worker.py --execute --limit 20`, generate
`/tmp/wecom-callback-processing.json` with
`check_wecom_callback_processing_evidence.py --pressure-evidence-file`, save
`/tmp/wecom-callback-public-state.json` with
`check_wecom_callback_public_state.py`, save
`/tmp/wecom-callback-deploy-smoke.json` with
`check_wecom_callback_deploy_smoke.py`, and pass
all files to
`check_wecom_callback_permanent_fix_readiness.py --pressure-evidence-file`
together with `--ingestion-evidence-file`, `--processing-evidence-file`,
`--worker-isolation-evidence-file`, and
`--downstream-worker-isolation-evidence-file`,
`--internal-event-worker-isolation-evidence-file`, `--rollback-evidence-file`,
plus `--public-state-evidence-file` and `--deploy-smoke-evidence-file`. In the generated cutover plan, this is the
`final_readiness` command group and should be run after rollback-drill evidence,
not inside the pressure-probe step.

Objective coverage gate:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_wecom_callback_objective_coverage.py \
  --readiness-file example output path (wecom-callback-readiness.json)
```

Expected after full production completion: `local_contract_ready=true`,
`production_completion_ready=true`, `ok=true`, and every
`objective_requirements.*.local_evidence_ok` plus production-required
`objective_requirements.*.production_evidence_ok` is true.

## Open Risks

- Production is still protected by emergency nginx quick ACK, which means valid callback business processing is still bypassed.
- Historical callbacks acknowledged by nginx quick ACK cannot be replayed from `webhook_inbox` because they never entered the application.
- Production 1200/min callback pressure test has not been run against the isolated 5002 ingress; the probe script is available locally, but evidence still must be collected after approved cutover.
- Live `webhook_inbox` backlog health has not been proven in production after cutover.
- Real outbound effects remain intentionally gated; enabling realtime dispatch for welcome/tag/profile requires a separate production approval.
- The deploy workflow now installs and starts the 5002 callback ingress service and callback worker timer, but host-local proof that the current production host has applied that workflow is still missing. nginx cutover remains a manual, checker-gated production step.

## Completion Criteria

The permanent fix can be considered complete only after production evidence
proves all of the following:

- nginx quick ACK removed from callback routes
- callback routes proxy to isolated `127.0.0.1:5002`
- callback routes include `limit_req`, `limit_conn`, and 429 overload status
- valid callbacks enqueue into `webhook_inbox` and return app-level ACK
- DB enqueue failure does not fake ACK
- worker failures retry/dead-letter without affecting HTTP ACK
- admin/sidebar pages remain available during callback pressure
- downstream external-effect worker outage does not affect callback ACK or sampled pages
- internal-event worker outage does not affect callback ACK or sampled pages
- `webhook_inbox` backlog health is within readiness thresholds after pressure
- dead-letter rows are visible and replayable from `/admin/webhook-inbox`
- real external sends occur only through `external_effect_job`
- rollback to emergency quick ACK is tested or ready
