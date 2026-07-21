# Data Health Checks

PR #19 turns the existing table and identity governance checks into a Next-native admin diagnostic surface.

## API

- `GET /api/admin/data-health/summary`
- `GET /api/admin/data-health/checks`
- `GET /api/admin/data-health/checks/{check_id}`

Responses use only check metadata, counts, table names, and remediation hints. They must not expose raw payloads, phone numbers, OpenIDs, external user IDs, or other identity fields outside the existing identity boundary.

## Initial Checks

Green static checks:

- `identity_legacy_column_guard`
- `table_lifecycle_manifest_guard`
- `retired_table_runtime_reference_guard`

Live schema drift check:

- `schema_drift_guard`

Registered runtime data probes:

- `unionid_orphan_fact_guard`
- `identity_resolution_queue_backlog`
- `projection_freshness_customer_read_model`
- `broadcast_job_blocked_backlog`
- `external_effect_failed_retryable_backlog`
- `deprecated_execution_settings_present`
- `fake_stub_route_exposed`
- `external_effect_approved_not_queued`
- `questionnaire_submission_without_user_guard`
- `payment_order_without_user_guard`
- `customer_360_freshness_guard`

All registered operational probes execute count-only, production-safe SQL when
`DATABASE_URL` is configured. They report `not_applicable` only in offline
environments without a database. `schema_drift_guard` compares
`information_schema.columns` with the lifecycle manifest and fails on missing
declared physical tables, unregistered live tables, retired physical tables,
missing canonical owners, missing PII levels, or missing queue status enum
metadata.

Relations imported from the pre-convergence production database use the
explicit `legacy` lifecycle. They are registered so they cannot appear as
unmanaged drift, but unlike Next-owned physical lifecycles their absence is not
an error. A later mutation or retirement must first assign a concrete owner and
use a reviewed migration.

`customer_360_freshness_guard` compares the latest identity, paid-order,
questionnaire, and message source timestamps with the most recent managed
customer read-model refresh. Evidence contains only aggregate lag minutes and
never raw identity values or payloads.

`questionnaire_submission_without_user_guard` treats a missing `unionid` as a
release blocker only when the submission is outside the durable
`questionnaire.submitted` outbox/event lineage or when an identity-dependent
Webhook/tag External Effect already exists. A submission that remains inside
that durable lineage with no such effect is an explicit quarantine state: it is
reported in aggregate evidence but does not block a release. A non-empty
`unionid` that is absent from `crm_user_identity` remains a red condition.

Questionnaire identity and continuation health uses the production auto-execute
cutover (`2026-07-13 16:20:00 UTC`). Shadow-only rows before that instant stay in
historical evidence; only submissions accepted after the worker became the runtime
owner can fail the current continuation guard.

## Status Semantics

- `ok`: check passed with current evidence.
- `warn`: check found a non-blocking operational risk.
- `fail`: check found a red condition that should block migration/release work.
- `not_applicable`: the runtime has no configured database, so a live probe cannot run.

The managed customer read model is refreshed through durable source events and
the coalesced `customer_read_model_refresh_intent`. The compatibility timer may
write an intent while the legacy generation remains the active owner, but it
never rebuilds the projection inline and is retired at the PR-3 generation
cutover. Singleton refresh evidence is stored in
`customer_read_model_refresh_state`.

`projection_freshness_customer_read_model` enforces projection population and
list/detail/managed-refresh count consistency. The wall-clock age of an
otherwise consistent projection is diagnostic only: elapsed time without a
source change is not data staleness. `customer_360_freshness_guard` remains the
release-blocking source-of-truth check and compares the latest identity, order,
questionnaire, and message facts with the last successful managed refresh.

## Data Quality Registry

Phase 7 starts turning data health into an operator-readable issue list. The
registry lives in `aicrm_next.data_health.quality_registry` and is metadata-only:
it defines the rule IDs, groups, source tables, thresholds, and remediation
language that later admin APIs and scheduled snapshots can execute through
production-safe read probes.

Registry API:

- `GET /api/admin/data-quality/summary`
- `GET /api/admin/data-quality/groups`
- `GET /api/admin/data-quality/checks`
- `GET /api/admin/data-quality/checks/{check_id}`

These endpoints expose only registry metadata. They do not connect to the
production database and do not evaluate rule status yet.

Admin dashboard:

- `GET /admin/data-quality`

The dashboard groups rules by operator domain and displays rule severity,
threshold, source table metadata, and remediation text from the registry.

Scheduled snapshot entrypoint:

- `scripts/run_data_quality_snapshot.py`
- `aicrm_next.background_jobs.data_quality_snapshot.run_scheduled_data_quality_snapshot`

The scheduled entrypoint currently generates a registry snapshot payload for
cron/systemd orchestration. It reports `database_probe_executed=false` and
`persistence_status=not_configured`; a later PR must attach production-safe
read probes and persistence before it can become a historical DQ snapshot table.

## Development Guardrails

`tools/check_sql_static_guard.py` scans Python SQL literals in production code,
scripts, tools, and guarded migrations. It blocks:

- runtime SQL references to tables marked `lifecycle=retired`;
- `CREATE TABLE` statements after the lifecycle guard baseline when the table is
  absent from `data_table_lifecycle_manifest.yml`;
- new business-table DDL that declares legacy identity columns such as
  `external_userid`, `openid`, `mobile_snapshot`, or `person_id` outside the
  explicit identity boundary.

The guard is part of `scripts/ci/run_architecture_gates.sh`.

`docs/architecture/repository_ownership.yml` declares repository capability
owners plus reviewed `table_reads` and `table_writes`. The companion
`tools/check_repository_ownership.py` guard requires every repository file to
appear in the registry, blocks declared reads of retired tables, and verifies
declared writes against lifecycle manifest write owners for tables already under
manifest governance.

Groups and registered rule counts:

- `identity`: 5 checks covering pending identity queues, conflicts, duplicate
  unionids, external contact to unionid collisions, and mobile to active unionid
  collisions.
- `payment`: 4 checks covering paid orders without CRM identity, paid orders
  without product code, refunds greater than paid amount, and local/provider
  status mismatches.
- `questionnaire`: 4 checks covering missing unionid, missing answers, answers
  referencing missing questions, and malformed final tags.
- `delivery`: 4 checks covering blocked broadcasts, retryable external-effect
  failures, failed outbound tasks, and stale queued/claimed work.
- `customer_projection`: 3 checks covering stale customer read models, stale
  Customer 360 projections, and timelines missing recent activity.

Until each rule gets a read-only probe, `probe_status` remains `needs_probe`.
The registry must not expose raw identity values, payload JSON, phone numbers,
OpenIDs, or customer content; it may expose only rule metadata and table names.
