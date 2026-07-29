# Internal Event Single Relay Owner And Fan-out Manifest Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure only the canonical Internal Event runtime can relay transactional outbox records, and persist an authoritative fan-out manifest so every relayed event has the complete, auditable consumer-run set.

**Architecture:** Split outbox relay ownership from scoped consumer execution. The canonical runtime builds and seals the complete consumer registry, relays an outbox record, snapshots the expected consumer specs on `internal_event`, creates all runs in the same transaction, and marks the outbox relayed only after completeness validation. Specialized schedulers remain consumer-only, while reconciliation repairs recent manifest-backed technical gaps without executing handlers or external effects.

**Tech Stack:** Python 3.12, FastAPI modular monolith, PostgreSQL, SQLAlchemy/psycopg, Alembic, pytest, systemd unit contracts.

---

### Task 1: Make scoped workers consumer-only by default

**Files:**
- Modify: `aicrm_next/platform/platform_foundation/internal_events/worker.py`
- Modify: `scripts/run_internal_event_worker.py`
- Modify: `aicrm_next/ai_audience_ops/scheduler.py`
- Test: `tests/test_internal_event_outbox.py`
- Test: `tests/test_ai_audience_ops.py`

**Step 1: Write the failing ownership regression tests**

Add tests proving that a default/scoped `InternalEventWorker` reports `relay_role=consumer_only`, leaves a pending payment outbox untouched, and that an explicit canonical owner can relay it.

**Step 2: Run the tests to verify failure**

Run: `pytest tests/test_internal_event_outbox.py tests/test_ai_audience_ops.py -q`

Expected: new assertions fail because every worker currently calls `relay_due()`.

**Step 3: Implement the minimal role split**

Add an explicit `relay_role` (`owner` or `consumer_only`) to `InternalEventWorker`, default it to fail-closed `consumer_only`, instantiate `InternalEventOutboxRelay` only for the owner, and return a structured disabled relay result for consumer-only preview/execute paths. Make `scripts/run_internal_event_worker.py` the explicit owner; keep AI Audience consumer-only.

**Step 4: Re-run focused tests**

Run: `pytest tests/test_internal_event_outbox.py tests/test_ai_audience_ops.py -q`

Expected: ownership tests pass and scoped consumers still execute existing runs.

### Task 2: Seal the canonical fan-out contract

**Files:**
- Modify: `aicrm_next/platform/platform_foundation/internal_events/consumer_registry.py`
- Modify: `aicrm_next/internal_event_composition.py`
- Modify: `aicrm_next/platform/platform_foundation/internal_events/outbox.py`
- Test: `tests/test_internal_event_outbox.py`
- Test: `tests/test_internal_events_payment_slice.py`

**Step 1: Write failing contract tests**

Cover deterministic sorted manifests, stable hashes, rejection of mutation after sealing, rejection of relay with a partial/unsealed registry, and canonical payment fan-out containing `webhook_order_paid_consumer`.

**Step 2: Run the tests to verify failure**

Run: `pytest tests/test_internal_event_outbox.py tests/test_internal_events_payment_slice.py -q`

Expected: failures for missing sealing/manifest APIs.

**Step 3: Implement the contract catalog in the registry**

Add registry sealing and deterministic per-event manifest generation from consumer name, type, and max attempts. Seal the full registry at the end of `build_internal_event_consumer_registry()`. Require an authoritative sealed registry for outbox relay.

**Step 4: Re-run focused tests**

Run: `pytest tests/test_internal_event_outbox.py tests/test_internal_events_payment_slice.py -q`

Expected: contract tests pass.

### Task 3: Persist and enforce the fan-out manifest atomically

**Files:**
- Create: `migrations/versions/0122_internal_event_fanout_manifest.py`
- Modify: `aicrm_next/platform/platform_foundation/internal_events/models.py`
- Modify: `aicrm_next/platform/platform_foundation/internal_events/repository_support.py`
- Modify: `aicrm_next/platform/platform_foundation/internal_events/repository.py`
- Modify: `aicrm_next/platform/platform_foundation/internal_events/repository_memory.py`
- Modify: `aicrm_next/platform/platform_foundation/internal_events/outbox.py`
- Test: `tests/test_internal_event_outbox.py`
- Test: `tests/test_database_bootstrap.py`

**Step 1: Write failing persistence/invariant tests**

Test that relay stores `fanout_manifest_version`, `fanout_manifest_hash`, `fanout_manifest_json`, and `expected_consumer_count`; a manifest mismatch fails retryably; and an incomplete run set cannot result in `status=relayed`.

**Step 2: Run the tests to verify failure**

Run: `pytest tests/test_internal_event_outbox.py tests/test_database_bootstrap.py -q`

Expected: schema/model assertions fail.

**Step 3: Add the expand-only migration and models**

Add non-null defaulted manifest columns to `internal_event`, update public model mapping, and keep old rows valid with an empty legacy manifest.

**Step 4: Enforce the transaction invariant**

During relay, create/find the event, bind the manifest once, create all expected runs idempotently, query the persisted run names, reject manifest mismatch or missing expected names, then mark the outbox relayed. Mirror this behavior in the in-memory repository.

**Step 5: Re-run focused tests**

Run: `pytest tests/test_internal_event_outbox.py tests/test_database_bootstrap.py -q`

Expected: persistence and completeness tests pass.

### Task 4: Make reconciliation manifest-aware

**Files:**
- Modify: `aicrm_next/platform/platform_foundation/internal_events/reconciliation/outbox.py`
- Modify: `docs/runbooks/internal_event_outbox_reconciliation.md`
- Test: `tests/test_internal_event_outbox.py`

**Step 1: Write failing repair tests**

Add a manifest-backed event with one missing run and prove repair creates only the manifest-declared run, does not execute a handler, does not increment attempts, and is idempotent. Retain the cutover-scoped fallback for legacy events with empty manifests.

**Step 2: Run the test to verify failure**

Run: `pytest tests/test_internal_event_outbox.py -q`

Expected: reconciliation still derives every expectation from the current payment registry.

**Step 3: Implement manifest-aware diagnostics and repair**

Prefer each event's stored manifest. Use current canonical payment specs only for legacy manifest-less events inside the existing cutover boundary. Keep repair technical-only and provider-free.

**Step 4: Re-run the test**

Run: `pytest tests/test_internal_event_outbox.py -q`

Expected: all reconciliation tests pass.

### Task 5: Lock the deployment and architecture contracts

**Files:**
- Modify: `deploy/openclaw-internal-event-worker.service`
- Modify: `deploy/openclaw-ai-audience-scheduler.service`
- Modify: `deploy/production_runtime_units.json`
- Modify: `docs/architecture/data_table_lifecycle_manifest.yml`
- Modify: `docs/queue/ai-audience-scheduler.md`
- Create: `docs/adr/2026-07-16-single-internal-event-relay-owner.md`
- Modify: `docs/ci/test_scope_manifest.yml`
- Test: `tests/test_deploy_workflow_contract.py`
- Test: `tests/test_database_bootstrap.py`

**Step 1: Write failing deploy contract tests**

Assert that the canonical unit declares the owner role, the AI Audience unit declares consumer-only, only the canonical script constructs an owner worker, and the migration/manifest files trigger the PostgreSQL/internal-event test scope.

**Step 2: Run tests to verify failure**

Run: `pytest tests/test_deploy_workflow_contract.py tests/test_database_bootstrap.py tests/test_select_test_scope.py -q`

Expected: role/documentation/scope assertions fail.

**Step 3: Update runtime contracts and ADR**

Document the single-owner decision, failure modes, webhook safety boundary, manifest semantics, reconciliation behavior, and previous-release rollback. Do not enable real webhook execution.

**Step 4: Re-run contract tests**

Run: `pytest tests/test_deploy_workflow_contract.py tests/test_database_bootstrap.py tests/test_select_test_scope.py -q`

Expected: contract tests pass.

### Task 6: Final verification and GitHub publication

**Files:**
- Verify all modified files only.

**Step 1: Run the focused regression bundle**

Run: `pytest tests/test_internal_event_outbox.py tests/test_internal_events_payment_slice.py tests/test_internal_events_worker_pair_allowlist.py tests/test_ai_audience_ops.py tests/test_deploy_workflow_contract.py tests/test_database_bootstrap.py tests/test_select_test_scope.py -q`

Expected: all pass.

**Step 2: Run architecture and hygiene gates**

Run: `scripts/ci/run_architecture_gates.sh`

Run: `ruff check <modified-python-files>`

Run: `git diff --check`

Expected: all exit zero.

**Step 3: Review scope and commit**

Stage only the P0+P1 implementation, tests, migration, plan, ADR, and contract docs. Commit with a terse Chinese message.

**Step 4: Push and create the draft PR**

Push `codex/internal-event-single-relay-fanout` to `origin` and open a Chinese draft PR targeting `main`, with Summary, Architecture boundary, Safety/non-goals, Verification, Risk/rollback, and Next action.
