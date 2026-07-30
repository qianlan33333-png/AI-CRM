# Data Health Checks

PR #19 turns the existing table and identity governance checks into a Next-native admin diagnostic surface.

## API

- `GET /api/admin/data-health/summary`
- `GET /api/admin/data-health/checks`
- `GET /api/admin/data-health/checks/{check_id}`

Responses use only check metadata, counts, table names, and remediation hints. They must not expose raw payloads, phone numbers, OpenIDs, external user IDs, or other identity fields outside the existing identity boundary.

## Snapshot rollout

The first snapshot slice adds `data_health_snapshot` and the explicit
`scripts/ops/refresh_data_health_snapshot.py` writer. The writer completes and
validates every registered check before atomically replacing one singleton
row. An exception, empty result, duplicate check ID, or failed transaction
leaves the previous generation untouched. The table stores aggregate check
results, release provenance, duration, and the refresh timestamp; it contains
no customer identifier or message payload.

The foundation release did not change the three HTTP APIs or schedule the
writer. The second slice registers an active systemd timer that refreshes the
aggregate singleton every 15 minutes. It uses a dedicated PostgreSQL
`application_name`, a one-connection pool, bounded statement/lock/transaction
timeouts, and a 240-second process boundary. The service completes all checks
before its short atomic upsert, so a timeout or failed check run preserves the
previous complete generation. The online read cutover remains a separate
release so runtime activation and API compatibility can be verified and rolled
back independently.

The third slice moves all three online APIs to one indexed singleton snapshot
read per request. Their successful JSON bodies remain unchanged. Production
never falls back to running the 16 live checks inline: a missing, invalid, or
older-than-25-minutes snapshot returns a privacy-safe `503` error so a stopped
timer is visible instead of silently restoring the original high-cost path.
Offline fixture/test runtimes without a production repository keep the direct
check runner for local contract tests only. Detail lookup scans only the 16
already-loaded aggregate results and does not execute check prefixes eagerly.

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

`external_effect_failed_retryable_backlog` separates delivery failures from
deterministic business outcomes.  A WeChat refund `NOT_ENOUGH` result is reported
as a completed business rejection, not a system failure, only when strict
cross-table evidence proves one provider call, a received provider result, no
refund execution, a synchronized local refund record, and no replay.  Missing
or contradictory evidence remains fail-closed.

The same fail-closed rule applies to a WeCom private-message `84061` result. It
is a completed send flow with a business rejection because the external-contact
relationship no longer exists, not an infrastructure failure, only when the
production job and its sole attempt prove the real provider call, exact response,
settled lease state, and absence of any successful replay.

One operator-authorized WeCom group-message `40058` terminal from 2026-07-30 is
excluded only through an exact `terminal_readonly` row in the existing immutable
`queue_history_classification` ledger. The classifier requires the single
provider attempt, exact source route and one-minute window, provider `errcode`,
redacted target hash, release SHA, and explicit no-replay/no-success claims. It
does not change the failed job or attempt and performs no provider request.

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
list/detail/managed-refresh count consistency against the currently active
primary/shadow generation. The wall-clock age of an
otherwise consistent projection is diagnostic only: elapsed time without a
source change is not data staleness. `customer_360_freshness_guard` remains the
release-blocking source-of-truth check and compares the latest identity, order,
questionnaire, and message facts with the last successful managed refresh.


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

Production snapshot provenance is pinned before the long-running checks begin,
so an in-flight timer cannot be relabelled when a deploy switches the checkout.
After the new Web release is healthy, the deploy creates and validates one
generation for that exact release SHA before running the all-green admin smoke.
