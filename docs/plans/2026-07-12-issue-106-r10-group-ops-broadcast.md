# R10 Group Ops and Broadcast Reliability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Issue:** #106
**Parent:** #67
**Base:** `main@d36b4482de6f068fe7cb50282ac9e9a336534ab7`

**Goal:** Remove the duplicate P1 Group Ops control plane, converge Group Ops actions on one durable delivery path, and make broadcast delivery state/evidence crash-safe without triggering real sends during development or reconciliation.

**Architecture:** The existing `/admin/automation-conversion/group-ops` surface remains the sole Group Ops control plane. Private-message actions enqueue `broadcast_jobs`; WeCom group sends and outbound webhooks enqueue typed `external_effect_job` rows; request and scheduler paths never call providers. PostgreSQL idempotency constraints are the concurrency boundary. The broadcast worker uses claim -> dispatching -> one atomic finalization transaction, and ambiguous post-provider outcomes stop in `unknown_after_dispatch` for manual reconciliation rather than automatic resend.

**Tech Stack:** FastAPI, SQLAlchemy sessions, psycopg/PostgreSQL, Alembic, pytest, Node frontend contract tests, YAML/JSON architecture manifests, systemd deployment inventories.

---

## Architecture preflight decisions

- Capability owner: `aicrm_next.automation.automation_engine.group_ops` owns Group Ops plans, triggers, and internal action planning.
- Delivery owners: `aicrm_next.automation.background_jobs` owns final `broadcast_jobs` dispatch state; `aicrm_next.platform.platform_foundation.external_effects` owns group-message and webhook effects.
- Formal management routes live under `/api/admin/automation-conversion/group-ops/*` and use admin session/capability policy.
- Purpose-bound integration routes remain `/api/automation/group-ops/webhooks/{webhook_key}` and `/api/automation/group-ops/broadcast`; all other compatibility management aliases are removed and must return 404.
- The unlinked `/admin/p1/group-ops-workspace` page and its draft/governance APIs are retired. Its historical tables are preserved as read-only audit data and marked `retired`/`drop_candidate`; R10 performs no table drop or row mutation.
- `openclaw-automation-ops-scheduler.timer` may become autostart only after PostgreSQL concurrency and planner-failure tests prove exactly-one planning and truthful non-zero failure exit.
- Development, CI, count-only reconciliation, and deployment verification must not invoke WeCom or outbound webhook providers.
- Existing in-memory fakes are useful unit fixtures but cannot prove transaction, lease, conflict, or crash semantics; every state-machine boundary needs a real PostgreSQL test.

## Frontend preflight

- Reference page: `aicrm_next/automation/automation_engine/group_ops/templates/admin_console/group_ops.html` at `/admin/automation-conversion/group-ops` remains the canonical daily workspace.
- Reference assets: `aicrm_next/automation/automation_engine/group_ops/static/admin_console/group_ops.js` and `group_ops.css` remain the canonical Group Ops frontend implementation.
- Reference shell: existing `aicrm_next/admin_shell` page layout, navigation, session, and capability behavior are retained.
- Reused service/API: the existing formal Group Ops admin façade and material-picker integration remain; missing members/audience/segmentation/execution reads move to formal admin URLs rather than a new client wrapper.
- No component is added. The frontend work is deletion of a duplicate P1 page plus URL convergence in the canonical page.
- Information architecture stays list/workspace-first with existing detail/config behavior; no page-level title or description is duplicated.

## Task 1: Lock the retirement and route boundary with failing tests

**Files:**

- Add: `tests/test_group_ops_control_plane_retirement.py`
- Modify: `tests/test_group_ops_admin_pages_next_native.py`
- Modify: `tests/test_group_ops_frontend_contract.py`
- Modify: `tests/test_p1_diagnostics_removal_contract.py`
- Modify: `docs/architecture/route_ownership_manifest.yml`
- Modify: `docs/architecture/runtime_contract_inventory.json`
- Modify: `docs/ci/test_scope_manifest.yml`

**Step 1: Write the failing control-plane contract tests**

Assert that:

- `/admin/p1/group-ops-workspace` returns 404.
- Every `/api/admin/p1/group-ops-workspace/*` API returns 404.
- Compatibility management routes under `/api/automation/group-ops/*` return 404.
- `/api/automation/group-ops/webhooks/{webhook_key}` and `/api/automation/group-ops/broadcast` remain registered and require their purpose-bound credentials.
- Members, audience, segmentation, and execution read APIs are available only under `/api/admin/automation-conversion/group-ops/*`.
- The canonical Group Ops page references no P1 bundle and no management compatibility URL.

**Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_group_ops_control_plane_retirement.py \
  tests/test_group_ops_admin_pages_next_native.py \
  tests/test_group_ops_frontend_contract.py \
  tests/test_p1_diagnostics_removal_contract.py
```

Expected: failures identify the still-registered P1 page/APIs and compatibility management aliases.

## Task 2: Remove the P1 runtime and converge the formal control plane

**Files:**

- Modify: `aicrm_next/router_registry.py`
- Modify: `aicrm_next/admin_shell/routes.py`
- Modify: `aicrm_next/admin_shell/navigation.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/api.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/static/admin_console/group_ops.js`
- Delete: `aicrm_next/admin_shell/templates/admin_shell/p1_group_ops_workspace.html`
- Delete: `aicrm_next/automation/automation_engine/group_ops/draft_api.py`
- Delete: `aicrm_next/automation/automation_engine/group_ops/draft_repository.py`
- Delete: `aicrm_next/automation/automation_engine/group_ops/draft_service.py`
- Delete: `aicrm_next/automation/automation_engine/group_ops/governance_api.py`
- Delete: `aicrm_next/automation/automation_engine/group_ops/governance_repository.py`
- Delete: `aicrm_next/automation/automation_engine/group_ops/governance_service.py`
- Delete: `aicrm_next/frontend_compat/static/admin_console/p1/p1_group_ops_workspace/`
- Delete: `scripts/diagnose_p1_group_ops_workspace_bridge_acceptance.py`
- Delete: `tests/frontend/p1_group_ops_workspace.test.mjs`
- Delete: `tests/test_p1_group_ops_workspace_bridge_hardening.py`
- Delete: `tests/test_p1_group_ops_workspace_draft_api.py`
- Delete: `tests/test_p1_group_ops_workspace_final_closeout.py`
- Delete: `tests/test_p1_group_ops_workspace_frontend_contract.py`
- Delete: `tests/test_p1_group_ops_workspace_governance_api.py`
- Delete: `tests/test_p1_group_ops_workspace_production_validation_remediation.py`
- Modify: `tests/test_group_ops_workspace_draft_migration.py`
- Modify: `tests/test_group_ops_workspace_governance_migration.py`
- Preserve: `docs/reports/p1_group_ops_workspace_*.md` and `docs/rfcs/p1_group_ops_workspace_*.md` as historical records.

**Step 1: Add formal read façades before removing aliases**

Move the members, audience, segmentation, and executions handlers to `/api/admin/automation-conversion/group-ops/*`. Reuse the same application/repository functions and admin capability policy; do not fork DTOs or query logic.

**Step 2: Remove duplicate management routes**

Delete only management aliases from `group_ops/api.py`. Preserve the webhook callback and bearer-token broadcast integration routes.

**Step 3: Retire the P1 runtime**

Remove router registrations, page route/template, JS modules, draft/governance repositories/services, bridge diagnostics, and their implementation-specific API/frontend tests. Do not drop or mutate P1 tables. Retain the two historical migration tests and change their assertions to prove that old schema remains readable while no active runtime writer or route points to it.

**Step 4: Update architecture inventories**

- Remove retired P1 routes/runtime modules.
- Mark the seven P1 tables `retired`, `drop_candidate: true`, and read-only with no active write owner.
- Map their replacement to the canonical `automation_group_ops_*`, `broadcast_jobs`, and `external_effect_job` tables.
- Add the retirement tests to the Group Ops CI scope.

**Step 5: Run targeted GREEN checks**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_group_ops_control_plane_retirement.py \
  tests/test_group_ops_admin_pages_next_native.py \
  tests/test_group_ops_frontend_contract.py \
  tests/test_group_ops_plans_api.py \
  tests/test_group_ops_webhook_api.py \
  tests/test_group_ops_token_broadcast_api.py \
  tests/test_p1_diagnostics_removal_contract.py
node --test tests/frontend/*.test.mjs
```

Expected: one canonical management surface, both external integration routes protected, and no P1 runtime reference.

## Task 3: Make Group Ops planning single-path and concurrency-safe

**Files:**

- Modify: `aicrm_next/automation/automation_engine/group_ops/application.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/action_dispatcher.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/action_port.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/external_effects.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/scheduler.py`
- Delete: `aicrm_next/automation/automation_engine/group_ops/duplicate_checker.py`
- Modify: `aicrm_next/automation/background_jobs/automation_ops_scheduler.py`
- Modify: `deploy/production_runtime_units.json`
- Modify: `tests/test_group_ops_scheduler.py`
- Modify: `tests/test_group_ops_external_effect_queue.py`
- Modify: `tests/test_automation_ops_scheduler.py`
- Delete: `tests/test_group_ops_duplicate_checker.py`
- Add: `tests/test_group_ops_scheduler_postgres_concurrency.py`

**Step 1: Write failing action-mapping tests**

Cover this exact matrix:

| Action | Durable destination | Provider in request/scheduler | Extra effect |
| --- | --- | --- | --- |
| `enqueue`, `publish_task`, `send_message` | one private `broadcast_jobs` row | no | no |
| `send_group_message`, `group_notice` | one WeCom-group `external_effect_job` | no | no |
| `webhook_notify` | one outbound-webhook `external_effect_job` | no | no |
| `record_only`, `add_to_audience` | internal state only | no | no |

Also assert planner/database failures propagate into `ok=false`, an error summary, and process exit 1.

**Step 2: Remove shadow/legacy mode branches**

Delete the legacy/shadow/external-effect environment mode and broad `except Exception: return None`. Return the durable job or raise a typed planning error. Do not emit a success audit when persistence failed.

**Step 3: Remove check-then-plan correctness**

Delete `GroupOpsDuplicateChecker` from the scheduler. Let the existing unique `(tenant_id, idempotency_key)` constraint and `ON CONFLICT DO NOTHING` establish exactly-one planning. A conflict returns the authoritative existing row.

**Step 4: Eliminate double planning**

Narrow `group_ops_effect_action_type()` to group/webhook effect actions. Private actions dispatch only to the broadcast queue; group actions use the WeCom-group effect type; webhook actions use the outbound-webhook type.

**Step 5: Prove PostgreSQL concurrency and failure truth**

Use two transactions/processes synchronized by a barrier against an isolated PostgreSQL database. Both schedulers select the same due action; assert one durable job, one stable idempotency key, no provider invocation, and truthful summaries for winner/conflict. Inject database failure and assert no false success.

**Step 6: Activate the scheduler timer only after tests pass**

Move `openclaw-automation-ops-scheduler.timer` from `approval_required` to `active_autostart` in `deploy/production_runtime_units.json`. Keep real execution disabled through test-server provider configuration.

**Step 7: Run targeted tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_group_ops_scheduler.py \
  tests/test_group_ops_scheduler_postgres_concurrency.py \
  tests/test_group_ops_external_effect_queue.py \
  tests/test_group_ops_queue_contract.py \
  tests/test_automation_ops_scheduler.py
```

Expected: exactly-one planning, no swallowed errors, and no direct provider call.

## Task 4: Add the broadcast delivery state machine migration

**Files:**

- Add: `migrations/versions/0103_broadcast_delivery_state_machine.py`
- Add: `tests/test_broadcast_delivery_state_machine_migration.py`
- Modify: `tests/test_group_ops_prod_schema_bootstrap.py`
- Modify: `docs/architecture/data_table_lifecycle_manifest.yml`
- Modify: `docs/architecture/external_effect_delivery_state_machine.md`

**Step 1: Write migration tests first**

Upgrade a production-shaped database from `0102` to `0103` and assert:

- `broadcast_jobs.status` permits `dispatching` and `unknown_after_dispatch` while retaining existing statuses.
- Evidence columns exist: `dispatch_started_at`, `side_effect_executed`, `provider_result_received`, `result_summary_json`, `reconciliation_required`, and `completed_at`.
- Claim/reclaim index excludes `dispatching` and `unknown_after_dispatch`.
- Existing rows remain readable and retain status.
- Downgrade removes only R10 additions and restores the prior check constraint.

**Step 2: Implement migration `0103`**

Use additive nullable/default-safe columns and explicit check constraints. Do not rewrite historical provider payloads or P1 data. Do not infer `sent` evidence for old rows; reconciliation reports it.

**Step 3: Run migration tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_broadcast_delivery_state_machine_migration.py \
  tests/test_group_ops_prod_schema_bootstrap.py
```

Expected: clean `0102 -> 0103`, clean empty bootstrap, and reversible schema-only downgrade.

## Task 5: Make broadcast dispatch/finalization atomic and crash-safe

**Files:**

- Modify: `aicrm_next/automation/background_jobs/broadcast_queue_worker.py`
- Modify: relevant adapter result DTO in the existing broadcast/private dispatch module
- Modify: `tests/test_broadcast_jobs_service.py`
- Modify: `tests/test_broadcast_jobs_wecom_private_dispatch.py`
- Add: `tests/test_broadcast_queue_worker_postgres_state_machine.py`
- Add: `tests/test_broadcast_queue_worker_postgres_faults.py`

**Step 1: Write RED repository state-machine tests**

Assert:

- Claim assigns a non-empty token only to `queued`/retry-due rows.
- Begin-dispatch transitions claimed row to `dispatching` before provider execution.
- `dispatching` and `unknown_after_dispatch` are never lease-reclaimed automatically.
- A finalizer with the wrong token or wrong state changes zero rows and cannot overwrite another worker.
- Failure finalization updates job, recipient, message, outbound task/event, and evidence in one transaction.
- The existing bug where clearing `claim_token` before projection updates leaves recipient/message stale is reproduced by the RED test.

**Step 2: Separate provider dispatch from persistence**

The dispatcher returns a redacted result DTO containing outcome, retry classification, `side_effect_executed`, `provider_result_received`, request evidence, response evidence, counts, and safe error fields. It performs no post-provider database write.

**Step 3: Add one atomic finalizer**

Within one PostgreSQL transaction:

1. Lock the `broadcast_jobs` row by `id`, `status='dispatching'`, and `claim_token`.
2. Insert/reuse the `outbound_tasks` evidence and append `broadcast_job_events`.
3. Update cloud recipient/message projections.
4. Update the job to `sent`, `simulated`, a known retry/terminal state, or `unknown_after_dispatch`.
5. Clear the token only after all dependent writes have succeeded.

If the provider may have executed but no trustworthy result exists, set `unknown_after_dispatch`, `reconciliation_required=true`, and never auto-retry. Fake provider results become `simulated`, never `sent`.

**Step 4: Inject crash boundaries**

Against PostgreSQL, fail at:

- before provider call;
- after provider returns but before outbound-task insert;
- after outbound-task insert;
- after recipient/message update;
- immediately before job commit.

Assert pre-provider failures are safely retryable when classified so; post-provider ambiguity never resends; transaction failures do not leave a false partial terminal projection.

**Step 5: Run state-machine tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_broadcast_jobs_service.py \
  tests/test_broadcast_jobs_wecom_private_dispatch.py \
  tests/test_broadcast_queue_worker_postgres_state_machine.py \
  tests/test_broadcast_queue_worker_postgres_faults.py \
  tests/test_run_broadcast_queue_worker.py
```

Expected: no automatic resend after ambiguous provider execution and no split terminal state.

## Task 6: Add count-only reconciliation and ownership enforcement

**Files:**

- Add: `scripts/ops/reconcile_group_ops_broadcast.py`
- Add: `tests/test_group_ops_broadcast_reconciliation.py`
- Add: `docs/ops/group_ops_broadcast_reconciliation.md`
- Modify: `docs/architecture/repository_ownership.yml`
- Modify: `docs/architecture/data_table_lifecycle_manifest.yml`
- Modify: `docs/architecture/high_risk_contract_inventory.yml`
- Modify: `docs/ci/test_scope_manifest.yml`

**Step 1: Write the no-side-effect diagnostics tests**

The default command reports aggregate counts only for:

- stale `dispatching` rows;
- `unknown_after_dispatch` rows;
- job/recipient/message projection mismatches;
- `sent` rows missing durable evidence/outbound tasks;
- duplicate idempotency keys;
- remaining P1 runtime flags or active ownership declarations.

Patch provider and consumer entry points to raise if called. Seed PII and assert it does not appear in stdout/stderr. Running twice must be read-only and identical.

**Step 2: Implement count-only mode**

Default behavior is count-only. No default repair, provider call, consumer call, payload dump, or per-recipient output is permitted. Any future repair mode requires a separate issue and explicit authorization.

**Step 3: Update ownership and high-risk coverage**

- Set broadcast finalization owner to the background-jobs repository boundary.
- Remove P1 runtime writers.
- Record Group Ops/broadcast success, failure, and concurrency test nodes in the high-risk inventory.
- Ensure changes to scheduler, broadcast worker, migration, and manifests select the correct PostgreSQL/architecture scopes.

**Step 4: Run diagnostics and architecture gates**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_group_ops_broadcast_reconciliation.py
.venv/bin/python scripts/ops/reconcile_group_ops_broadcast.py --help
.venv/bin/python scripts/ops/reconcile_group_ops_broadcast.py
.venv/bin/python scripts/ci/check_test_scope_manifest.py
.venv/bin/python scripts/ci/check_route_ownership.py
.venv/bin/python scripts/ci/check_repository_ownership.py
.venv/bin/python scripts/ci/check_data_table_lifecycle.py
.venv/bin/python scripts/ci/check_runtime_contract_inventory.py
```

Expected: diagnostics are count-only and architecture ownership is internally consistent.

## Task 7: Full local verification

**Step 1: Lint and diff hygiene**

Run the repository's Python formatter/linter and diff checker on changed files. Inspect `git diff --check` and confirm no generated secret, environment file, or PII is present.

**Step 2: Frontend verification**

Run:

```bash
node --test tests/frontend/*.test.mjs
```

Confirm the canonical Group Ops page has one title/description, no P1 bundle, and no compatibility management URL.

**Step 3: PostgreSQL and migration verification**

Run all R10 PostgreSQL tests against an isolated database, then run Alembic upgrade from `0102` to `head` and verify `alembic current` is `0103_broadcast_delivery_state_machine`.

**Step 4: Full regression**

Run:

```bash
.venv/bin/python -m pytest -q -n 4 --dist loadfile
```

Expected: entire Python suite green, followed by frontend, dependency audit, and all architecture gates.

## Task 8: PR, merge, exact-SHA deployment, and evidence

**Step 1: Commit and publish**

Commit only R10 files on `codex/issue-106-r10-group-ops`, push, and open a Chinese PR with `[full-ci]` in the title/body as required by the optimized CI selector.

PR body sections must be exactly present:

- Summary
- Architecture boundary
- Safety / non-goals
- Verification
- Risk / rollback
- Next action

Include `Closes #106` and parent `#67`.

**Step 2: Watch PR CI and fix only evidence-backed failures**

Require architecture, dependency, frontend, all Python shards, and aggregate result green. Merge only after all required checks pass.

**Step 3: Watch main CI and test deployment**

Record the merge SHA, successful main CI URL, and `Deploy to Test` URL. Verify the public `x-aicrm-release-sha`, server checkout, `.release-sha`, and Alembic head all equal that merge SHA/head.

**Step 4: Verify runtime without real sends**

On the test server, verify:

- `openclaw-automation-ops-scheduler.timer` is enabled/active;
- broadcast and External Effect worker units are enabled/active according to inventory;
- provider execution remains disabled/fake for test verification;
- running `scripts/ops/reconcile_group_ops_broadcast.py` produces aggregate count-only output and no external call.

Do not enqueue a real customer/group message and do not invoke a real webhook.

**Step 5: Post evidence and reassess parent Epic**

Comment on #106 with exact SHA, CI/deploy URLs, Alembic head, timer/worker state, and count-only results. Then audit #67, open the next scoped child issue if work remains, and continue without waiting for a user prompt.

## Rollback

1. Roll back the application release to the previous exact SHA.
2. Disable `openclaw-automation-ops-scheduler.timer` if scheduler truth cannot be guaranteed.
3. Preserve all queued, dispatching, unknown, outbound-task, event, and retired P1 audit rows.
4. Do not convert `unknown_after_dispatch` to retryable automatically.
5. Alembic downgrade is allowed only after confirming no rows use R10-only statuses/evidence fields.
6. Do not restore the P1 write control plane or legacy/shadow planner; a rollback uses the prior release only as an emergency operational measure.
