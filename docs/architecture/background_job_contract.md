# Webhook / Background Job Contract

This document defines the AI-CRM Next contract for webhook, callback, notify,
and scheduled job entrypoints. The contract is a stability guardrail: it does
not enable real external calls, does not migrate every existing route, and does
not change production deploy/systemd/nginx/env settings.

## Entry Principle

Webhook and callback routes should stay thin:

1. Validate signature or perform basic payload checks.
2. Build an idempotency key.
3. Record an inbound event or internal event.
4. Enqueue or create a background job.
5. Return quickly.
6. Avoid synchronous real external side effects in API/HTTP handlers.

External work must move through `internal_event`, background job, or
`platform_foundation.external_effects` workers. External adapters remain blocked,
fake, staging-disabled, or approval-gated unless a separate approved runtime PR
changes that behavior.

## Job Envelope

`aicrm_next.platform_foundation.background_jobs.contract.BackgroundJobContract`
defines the shared job envelope:

- `job_type`
- `source_route`
- `idempotency_key`
- `payload_schema_version`
- `attempt_count`
- `next_run_at`
- `status`: `pending`, `running`, `succeeded`, `failed`, `dead_lettered`
- `external_effect_key`
- `audit_context`
- `created_at` / `updated_at`
- `last_error` / `error_code`

The in-memory queue and worker in the contract module are test fixtures and
reference semantics. Production persistence continues to use existing
`internal_event` and `external_effect_job` repositories until a later migration
PR explicitly moves a business route.

## Route Inventory Guard

`tools/check_background_job_contract.py` validates the current route ownership
manifest for routes whose layer is `webhook` or whose path/name contains
`webhook`, `callback`, or `notify`.

The checker ensures:

- Every webhook-like route is registered in the route contract inventory.
- Each route has a concrete owner and rollback.
- `external_effects` and `data_source` match the documented contract.
- Routes with `external_effects=none` include rationale that the route records
  or enqueues work before any outbound side effect.

This prevents adding a new webhook/callback route without making its background
job contract explicit.

## Idempotency, Retry, And Dead Letter

Entrypoints should use explicit provider event IDs when available. Otherwise,
they should derive a deterministic idempotency key from `source_route` and a
safe payload summary. Duplicate webhook/event submissions must return the
existing job/event instead of creating another business job.

Workers must record attempts. Retryable failures remain auditable as `failed`;
once retry policy is exhausted, the job must become `dead_lettered` or map to
the existing terminal-failure state in `internal_event` / `external_effect_job`.

## Rollback

This contract is not read by runtime route handlers. If the checker blocks an
urgent fix, rollback by removing it from `scripts/ci/run_architecture_gates.sh`
or reverting the checker PR. Do not enable real external calls, production
deployment changes, or legacy runtime fallback as part of this rollback.
