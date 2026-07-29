# External Effect Queue Production Virtual Test Runbook

This runbook validates the External Effect Queue with synthetic loopback
webhooks on the current production domain. It must not touch real customers,
real WeCom sends, real tag mutations, real payment queries, Feishu, OpenClaw, or
MCP integrations.

## 1. Preflight

Confirm the deployed migration head and default disabled state:

```bash
.venv/bin/alembic heads
curl -sS "$BASE_URL/api/admin/external-effects/diagnostics" \
  | jq '{
    real_execution_enabled,
    test_receiver_enabled,
    test_execution_only,
    allowed_effect_types,
    current_base_url_detected
  }'
```

Expected before enabling test mode:

```json
{
  "real_execution_enabled": false,
  "test_receiver_enabled": false,
  "allowed_effect_types": []
}
```

Use `/api/admin/push-center/jobs` for business delivery inspection. For
queue-level diagnostics, use `/admin/api-docs` and the
`/api/admin/external-effects/troubleshooting/*` APIs. Queue monitoring pages are
intentionally not exposed.

## 2. Host Trust Boundary Check

Production loopback testing must not start until both the reverse proxy and the
application allowlist agree on the current production host.

Nginx must overwrite forwarded host headers on every location that proxies to
AI-CRM Next:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

The application must also restrict detected base hosts:

```bash
export AICRM_EXTERNAL_EFFECT_ALLOWED_BASE_HOSTS=www.youcangogogo.com,youcangogogo.com
```

Validate that a forged forwarded host cannot affect diagnostics:

```bash
curl -sS \
  -H 'X-Forwarded-Host: attacker.example.com' \
  -H 'X-Forwarded-Proto: https' \
  "$BASE_URL/api/admin/external-effects/diagnostics" \
  | jq '{current_base_url_detected}'
```

Expected:

```json
{
  "current_base_url_detected": "https://www.youcangogogo.com"
}
```

If this check fails, do not enable:

- `AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED`
- `AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE`

Also do not create test-loopback jobs or run run-due.

## 3. Enable The Test Receiver

Enable only the loopback receiver and test-only execution guard:

```bash
export AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED=1
export AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY=1
```

The receiver route is:

```text
POST /api/external-effects/test-receiver/{receiver_token}
```

Receiver URLs are generated from `X-Forwarded-Proto` and `X-Forwarded-Host`, or
from the current request URL. Do not hardcode a production domain.

## 4. Enable One Low-Risk Effect Type

For questionnaire loopback:

```bash
export AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE=1
export AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES=webhook.questionnaire_submission.push
```

For order-paid loopback, replace the allowlist with:

```bash
export AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES=webhook.order_paid.push
```

Never include WeCom, payment-query, Feishu, OpenClaw, or MCP effect types in the
allowlist.

## 5. Create A Test Loopback Job

Create a synthetic job through the admin API. Do not pass `webhook_url`; the
server generates the current-domain receiver URL.

```bash
curl -sS -X POST "$BASE_URL/api/admin/external-effects/test-loopback/jobs" \
  -H "X-Admin-Action-Token: $ADMIN_ACTION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "questionnaire_submission_push_success",
    "response_status": 200
  }' | jq
```

The response includes `receiver_url`, `job`, and `runbook_next_steps`.

## 6. Preview

Preview must happen before any execution:

Use an `automation_worker` short-lived JWT in `AICRM_ACCESS_TOKEN` (`audience=internal_worker`, `scope=write`); see [`../auth_client_credentials.md`](../auth_client_credentials.md).

```bash
curl -sS -X POST "$BASE_URL/api/admin/external-effects/run-due/preview" \
  -H "Authorization: Bearer $AICRM_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "batch_size": 1,
    "effect_types": ["webhook.questionnaire_submission.push"],
    "test_only": true
  }' | jq '{counts,dry_run,test_only,real_external_call_executed}'
```

Expected: `dry_run=true`, `test_only=true`,
`real_external_call_executed=false`.

## 7. Dry-Run

```bash
curl -sS -X POST "$BASE_URL/api/admin/external-effects/run-due" \
  -H "Authorization: Bearer $AICRM_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "batch_size": 1,
    "dry_run": true,
    "effect_types": ["webhook.questionnaire_submission.push"],
    "test_only": true
  }' | jq '{counts,dry_run,test_only,real_external_call_executed}'
```

Expected: no receipt and no real external call.

## 8. Execute One Test Job

Only after preview and dry-run match the expected candidate:

```bash
curl -sS -X POST "$BASE_URL/api/admin/external-effects/run-due" \
  -H "Authorization: Bearer $AICRM_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "batch_size": 1,
    "dry_run": false,
    "effect_types": ["webhook.questionnaire_submission.push"],
    "test_only": true
  }' | jq '{counts,dry_run,test_only,real_external_call_executed,items}'
```

The only acceptable real call target is this app's own test receiver URL.

## 9. Verify Receipt, Attempt, Diagnostics

```bash
curl -sS "$BASE_URL/api/admin/external-effects/test-receipts?job_id=$JOB_ID" | jq
curl -sS "$BASE_URL/api/admin/external-effects/jobs/$JOB_ID" | jq '{job,attempts}'
curl -sS "$BASE_URL/api/admin/external-effects/diagnostics" \
  | jq '{
    test_receiver_enabled,
    test_execution_only,
    test_receipt_count_24h,
    latest_test_receipt_at,
    real_external_call_executed_to_test_receiver_count
  }'
```

The receipt trace and idempotency key must match the job. The receipt
`payload_hash` proves the loopback receiver received the expected synthetic
payload.

## 10. Failure Tests

500 retryable:

```bash
curl -sS -X POST "$BASE_URL/api/admin/external-effects/test-loopback/jobs" \
  -H "X-Admin-Action-Token: $ADMIN_ACTION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"scenario":"questionnaire_submission_push_retry_500","response_status":500}'
```

Expected after test-only execution: `failed_retryable`, `next_retry_at` set,
attempt status code `500`, receipt present.

400 terminal:

```bash
curl -sS -X POST "$BASE_URL/api/admin/external-effects/test-loopback/jobs" \
  -H "X-Admin-Action-Token: $ADMIN_ACTION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"scenario":"order_paid_push_terminal_400","response_status":400}'
```

Expected after test-only execution: `failed_terminal`, no retry schedule, receipt
present.

Allowlist miss: leave the effect type out of
`AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES`. Expected: no receipt, no send, job blocked
by adapter gate.

Dry-run no send: keep `dry_run=true`. Expected: no receipt and
`real_external_call_executed=false`.

## 11. Rollback

Disable all virtual execution switches:

```bash
export AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE=0
export AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES=
export AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED=0
export AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY=0
```

Cancel any leftover test jobs:

```bash
curl -sS -X POST "$BASE_URL/api/admin/external-effects/jobs/$JOB_ID/cancel" \
  -H "X-Admin-Action-Token: $ADMIN_ACTION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

After rollback, run-due must no longer perform real execution.

## 12. Prohibited Actions

- Do not send real WeCom private messages, group sends, group messages, or
  welcome messages.
- Do not perform real WeCom tag mark or unmark.
- Do not use real customer `external_userid`, phone numbers, `openid`, or
  `unionid`.
- Do not run real payment queries.
- Do not call Feishu, OpenClaw, MCP, or arbitrary webhook URLs.
- Do not allow a request caller to provide a custom `webhook_url`.
