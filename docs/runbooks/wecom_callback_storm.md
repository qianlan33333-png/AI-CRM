# WeCom Callback Storm Runbook

## Purpose

The emergency nginx quick ACK rule restores `/health`, sidebar, and admin pages by returning `200 success` before the application sees WeCom callback POSTs. It is only a temporary protection mode. While it is enabled, callback business processing is suppressed and incoming events are not persisted by the application.

Acceptance audit:

- `docs/reports/wecom_callback_permanent_fix_acceptance_audit_20260627.md`

Chinese production-window checklist:

- `docs/runbooks/wecom_callback_production_cutover_zh.md`

## Check Current State

If host login is unavailable, run the public HTTP-only check from a local
checkout first:

```bash
export AICRM_CALLBACK_PUBLIC_BASE_URL="https://<public-host>"
.venv/bin/python scripts/ops/check_wecom_callback_public_state.py \
  --base-url "$AICRM_CALLBACK_PUBLIC_BASE_URL"
```

Emergency quick-ACK shape:

```json
{
  "ok": false,
  "user_facing_available": true,
  "admin_webhook_inbox_deployed": false,
  "invalid_callback_plain_success": true
}
```

This proves pages are reachable but the permanent callback repair is not live.
It is only a public gap detector; it does not replace the host-local nginx,
systemd, database, pressure, same-sample, and rollback evidence below.
After cutover, this public check should only become `ok=true` when the
webhook-inbox routes return a deployed/auth signal rather than 404/5xx and the
invalid callback probe is rejected with app-level 4xx, not nginx-level plain
`success` or upstream 5xx.

Run on the production host:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_callback_quick_ack_state.py --skip-probe
```

Expected emergency-mode shape:

```json
{
  "ok": true,
  "emergency_quick_ack_enabled": true,
  "business_processing_suppressed": true,
  "quick_ack_routes": [
    "/wecom/external-contact/callback",
    "/api/wecom/events"
  ],
  "callback_post_nginx_200_all": true
}
```

`business_processing_suppressed=true` means pages are protected, but callback business handling is still bypassed.
`callback_post_nginx_200_all=true` means both public callback POST probes still
return nginx-level plain `success`; if only one route does, the checker reports
`callback_post_nginx_200_any=true` and `callback_post_nginx_200_all=false`.

## Normal Recovery Sequence

1. Deploy the `webhook_inbox` migration.
2. Deploy the callback fast ACK route.
3. Start the isolated callback ingress runtime on `127.0.0.1:5002`.
4. Confirm callback ingress health:

   ```bash
   curl -sSf http://127.0.0.1:5002/health
   ```

5. Enable the WeCom callback inbox worker in dry-run first.
6. Enable the worker with `--execute` in small batches.
7. Confirm webhook inbox metrics are healthy: `due_count`, `failed_retryable_count`, `dead_letter_count`, and `oldest_received_age_seconds`.
8. Replace the nginx quick ACK rule with the isolated callback upstream and reload nginx.
9. Confirm invalid callback POST returns app-level 4xx rather than nginx-level `success` or upstream 5xx, while valid WeCom callbacks enqueue and ACK from the 5002 app path.

Before and after step 8, run:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_wecom_callback_ingress_cutover.py
```

After code deploy but before nginx cutover, run the local deploy smoke check:

```bash
python scripts/ops/check_wecom_callback_deploy_smoke.py \
  | tee /tmp/wecom-callback-deploy-smoke.json
```

This check should pass once 5001, 5002, and the webhook inbox admin routes are
deployed. It is intentionally weaker than the public-state/readiness checks:
quick ACK can still be active at this stage, and production completion remains
unproven until cutover, pressure, isolation, rollback, and public-state evidence
all pass. The smoke check requires distinct `--web-base-url` and
`--ingress-base-url` values; using the same public URL for both is rejected
because it cannot prove the 5001 web runtime and 5002 callback runtime are
separate.

Before cutover, `ready_for_cutover` should be `false` while quick ACK is still
enabled. After nginx is switched to `127.0.0.1:5002`, `ready_for_cutover` should
be `true`. After cutover, do not skip the invalid-callback probe: it verifies
that an invalid POST no longer receives nginx-level plain `success`.
The cutover checker validates both callback `location` blocks independently, so
a partial cutover where only one of `/wecom/external-contact/callback` or
`/api/wecom/events` proxies to 5002 must still fail.

After steps 3-8, run the aggregate readiness check:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_wecom_callback_permanent_fix_readiness.py
```

After the 5002 service, worker timer, webhook inbox schema, and nginx cutover are
all in place,
`/api/admin/webhook-inbox/metrics?provider=wecom&event_family=external_contact`
returns JSON `ok=true` with `queue_metrics.provider_distribution`,
`queue_metrics.route_distribution`, and `queue_metrics.recent_errors` arrays,
and
`/api/admin/webhook-inbox/items?provider=wecom&event_family=external_contact&status=pending_failed&limit=1`
returns JSON `ok=true` with an `items` array, and
`/api/admin/wecom/callback/reconciliation?limit=1` returns JSON `ok=true` with
a `recent_items` array,
`ready_for_production_cutover` should be `true`. At this stage
`ready_for_production_completion` is still expected to be `false` until pressure
evidence, `webhook_inbox` ingestion evidence, isolation evidence, public HTTP
state evidence, live `webhook_inbox` backlog health, and rollback-drill evidence
are all supplied.

To generate the exact approved-window command plan before touching production:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/prepare_wecom_callback_ingress_cutover.py
```

This command is dry-run only. It checks the local cutover assets and prints the
preflight, systemd install plus deploy-smoke, nginx cutover, worker-isolation
canary, downstream-worker isolation canary, internal-event worker isolation
canary, pressure-probe, public-state evidence, rollback, reapply-cutover,
rollback-drill evidence, and final-readiness command groups. The deploy-smoke
command runs inside `install_and_start` after 5002 and the persistent callback
worker service start. During rollback drill, run `rollback` first, then
`reapply_cutover_after_rollback`, then capture and validate
`/tmp/wecom-callback-rollback.json` with `rollback_drill_evidence`. Run
`final_readiness` only after that rollback evidence has been validated.
The nginx merge remains manual by design.

The worker-isolation canary intentionally stops
`openclaw-wecom-callback-inbox-worker.service`, sends one validated callback to
the 5002 ingress, saves `/tmp/wecom-callback-worker-isolation.json`, and then
starts the persistent worker again. Passing that canary proves the ACK owns only
the durable inbox insert and that worker downtime affects queue processing only.

The downstream-worker isolation canary stops `openclaw-external-push-worker.service`
if present, sends one validated callback, samples `/health`, sidebar, and admin
routes, saves `/tmp/wecom-callback-downstream-worker-isolation.json`, and starts
the worker again. Passing that canary proves downstream external-effect work does
not own callback ACK or page availability.

The internal-event worker isolation canary stops
`openclaw-internal-event-worker.timer` and
`openclaw-internal-event-worker.service`, sends one validated callback, samples
`/health`, sidebar, and admin routes, saves
`/tmp/wecom-callback-internal-event-worker-isolation.json`, and starts the
worker again. Passing that canary proves internal-event worker downtime only
creates backlog and does not own callback ACK or page availability.

After cutover, generate an approved valid callback sample from the current
callback env:

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

Then run the pressure probe with that generated sample:

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
  --duration-seconds 60
```

Expected evidence:

- `sample_validation.ok=true` before any pressure requests are sent
- `sample_validation.idempotency_key` is present for the validated sample
- requested and observed callback rate >= 1200/min
- callback P95 <= 200 ms
- callback P99 <= 500 ms
- `/health` P95 <= 100 ms
- `/sidebar/bind-mobile` P95 <= 300 ms
- pressure evidence keeps its configured target fields at or below those limits
- sampled admin/sidebar routes have no HTTP 5xx
- `real_external_call_executed=false`

Save the pressure-probe JSON, prove the sampled callback reached
`webhook_inbox`, run the inbox worker once, prove that the sampled row was
processed, and pass the evidence files into the aggregate readiness check:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_wecom_callback_ingestion_evidence.py \
  --pressure-evidence-file example output path (wecom-callback-pressure.json) \
  | tee /tmp/wecom-callback-ingestion.json

python scripts/run_wecom_callback_inbox_worker.py --limit 20
AICRM_WECOM_CALLBACK_INBOX_WORKER_EXECUTE=1 \
  python scripts/run_wecom_callback_inbox_worker.py --execute --limit 20

python scripts/ops/check_wecom_callback_processing_evidence.py \
  --pressure-evidence-file example output path (wecom-callback-pressure.json) \
  | tee /tmp/wecom-callback-processing.json

python scripts/ops/check_wecom_callback_public_state.py \
  --base-url http://127.0.0.1:5001 \
  | tee /tmp/wecom-callback-public-state.json

python scripts/ops/check_wecom_callback_deploy_smoke.py \
  | tee /tmp/wecom-callback-deploy-smoke.json
```

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_wecom_callback_permanent_fix_readiness.py \
  --pressure-evidence-file example output path (wecom-callback-pressure.json) \
  --ingestion-evidence-file example output path (wecom-callback-ingestion.json) \
  --processing-evidence-file example output path (wecom-callback-processing.json) \
  --worker-isolation-evidence-file example output path (wecom-callback-worker-isolation.json) \
  --downstream-worker-isolation-evidence-file example output path (wecom-callback-downstream-worker-isolation.json) \
  --internal-event-worker-isolation-evidence-file example output path (wecom-callback-internal-event-worker-isolation.json) \
  --rollback-evidence-file example output path (wecom-callback-rollback.json) \
  --public-state-evidence-file example output path (wecom-callback-public-state.json) \
  --deploy-smoke-evidence-file example output path (wecom-callback-deploy-smoke.json)
```

Production completion requires the pressure evidence, same-sample
`webhook_inbox` ingestion evidence, same-sample worker processing evidence,
worker-isolation canary evidence,
downstream-worker isolation evidence, internal-event worker isolation evidence,
rollback-drill evidence, public HTTP state evidence, deploy-smoke evidence, and
`webhook_inbox` health to be accepted by this readiness checker. Public state
evidence must show the webhook-inbox routes are deployed and invalid callback
POST is rejected with app-level 4xx on both `/wecom/external-contact/callback`
and `/api/wecom/events`, not nginx-level `success` or upstream 5xx.
Deploy-smoke evidence must show web health, ingress health, the 5002 ingress
callback routes for both `/wecom/external-contact/callback` and
`/api/wecom/events`, the webhook inbox admin page, JSON APIs, and detail
processing-chain route are deployed. Its `web_base_url` and `ingress_base_url`
must be distinct, normally `http://127.0.0.1:5001` and
`http://127.0.0.1:5002`.
The ingestion evidence must find a recent
`webhook_inbox` row for `sample_validation.idempotency_key` under
`provider=wecom` and `event_family=external_contact`. The processing evidence
must show the same row reached `status=succeeded`, has `finished_at`, and for
the default canary has `identity_sync_status=skipped` with no external effect
jobs. The readiness checker also emits `same_sample_evidence`; it must be
`ok=true`, proving the pressure, ingestion, and processing JSON all reference
the exact same `idempotency_key`. The default inbox health thresholds are
`due_count <= 100`, `failed_retryable_count = 0`, `dead_letter_count = 0`, and
`oldest_received_age_seconds <= 300`. Pressure and isolation evidence must
include `sample_validation.ok=true`, proving the supplied callback sample
decrypted with the current WeCom callback config before load was sent. With
accepted pressure evidence, accepted same-sample evidence, accepted ingestion evidence, accepted processing
evidence, accepted callback-worker/downstream/internal-event isolation evidence,
accepted rollback evidence, accepted public state evidence, accepted deploy-smoke evidence, and a healthy inbox, both
`ready_for_production_completion` and `ok` should be `true`. Without these files, the checker will warn that required
production evidence is still missing; with `--skip-db`, completion remains false
because live queue health is unproven.

For rollback evidence, generate the template and replace it with captured values
after an approved production rollback drill:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_wecom_callback_rollback_evidence.py --print-template \
  > /tmp/wecom-callback-rollback.template.json

python scripts/ops/check_wecom_callback_rollback_evidence.py \
  --evidence-file /tmp/wecom-callback-rollback.json
```

The rollback JSON must prove the nginx backup existed, nginx config test and
reload passed after restoring the backup, `/health` stayed healthy, quick ACK was
restorable, and the permanent 5002 cutover was re-applied after the drill.
The generated cutover plan also writes the active nginx backup path to
`/tmp/wecom-callback-cutover-backup-path`; the rollback command group reads that
file instead of depending on a shell variable from an earlier command group.
The generated `reapply_cutover_after_rollback` command group restarts the 5002
ingress and worker timer, re-applies the manual nginx cutover, and refreshes
`/tmp/wecom-callback-public-state.json` and
`/tmp/wecom-callback-deploy-smoke.json` before final readiness.

The generated callback canary defaults to `ChangeType=del_external_contact`.
That is intentional: it is a valid encrypted external-contact callback for ACK,
dedupe, and inbox-ingestion proof, while the worker records it as a non-entry
event and does not run identity sync, channel-entry projection, or external
effect planning.

For a single failed/dead-letter row, call
`POST /api/admin/webhook-inbox/{id}/dispatch` with `dry_run=true` and an admin
action token first. If the preview is acceptable, call the same route with a
versioned durable queue command and `dry_run=false`. `retry` only marks a row
due again; `dispatch` accepts the targeted replay command for that row only.

To inspect the 2026-06-27 incident window after the permanent cutover, query
the Webhook Inbox API with status `pending_failed` and the Beijing-time receive
window directly:

```bash
curl -sS 'http://127.0.0.1:5001/api/admin/webhook-inbox/items?provider=wecom&event_family=external_contact&status=pending_failed&received_from=2026-06-27T11:00&received_to=2026-06-27T11:20'
```

For a final requirement-by-requirement gate, save the readiness JSON and run:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_wecom_callback_objective_coverage.py \
  --readiness-file example output path (wecom-callback-readiness.json)
```

This command keeps local contract coverage separate from production completion
evidence.

Candidate callback upstream shape:

```nginx
# In nginx http{} scope:
limit_req_zone $binary_remote_addr zone=aicrm_wecom_callback_req:10m rate=30r/s;
limit_conn_zone $binary_remote_addr zone=aicrm_wecom_callback_conn:10m;

upstream aicrm_wecom_ingress {
    server 127.0.0.1:5002;
}

location = /wecom/external-contact/callback {
    limit_req zone=aicrm_wecom_callback_req burst=120 nodelay;
    limit_conn aicrm_wecom_callback_conn 20;
    limit_req_status 429;
    limit_conn_status 429;
    proxy_pass http://aicrm_wecom_ingress;
    proxy_connect_timeout 1s;
    proxy_send_timeout 3s;
    proxy_read_timeout 3s;
}

location = /api/wecom/events {
    limit_req zone=aicrm_wecom_callback_req burst=120 nodelay;
    limit_conn aicrm_wecom_callback_conn 20;
    limit_req_status 429;
    limit_conn_status 429;
    proxy_pass http://aicrm_wecom_ingress;
    proxy_connect_timeout 1s;
    proxy_send_timeout 3s;
    proxy_read_timeout 3s;
}
```

## Rollback

If the application callback path causes page latency or 5001 backlog saturation again, restore the nginx quick ACK backup and reload nginx:

```bash
sudo cp /etc/nginx/sites-enabled/youcangogogo.conf.bak-codex-callback-quick-ack-20260627T111716 /etc/nginx/sites-enabled/youcangogogo.conf
sudo nginx -t
sudo systemctl reload nginx
```

Then re-run:

```bash
cd /home/ubuntu/极简 crm
source /home/ubuntu/venvs/openclaw/bin/activate
set -a && source /home/ubuntu/.openclaw-wecom-pg.env && set +a
python scripts/ops/check_callback_quick_ack_state.py --skip-probe
```

## Permanent Direction

The target architecture is:

- Webhook Inbox: verify/decrypt/enqueue/ACK.
- WeCom Callback Worker: consume inbox and run channel-entry business handling.
- Internal Events: represent internal facts and drive projections.
- External Effects: own all real outbound calls and retries.
- Runtime Isolation: callback ingress and user/admin web runtime run separately.

Local deploy assets prepared for the isolated runtime:

- `aicrm_next.channels.channel_entry.ingress_app:app`
- `scripts/run_wecom_callback_ingress.py`
- `deploy/openclaw-wecom-callback-ingress.service`
- `deploy/nginx-wecom-callback-ingress.conf.example`
- `scripts/ops/check_wecom_callback_ingress_cutover.py`
- `scripts/ops/check_wecom_callback_permanent_fix_readiness.py`
- `scripts/ops/prepare_wecom_callback_ingress_cutover.py`
- `scripts/ops/probe_wecom_callback_pressure.py`
