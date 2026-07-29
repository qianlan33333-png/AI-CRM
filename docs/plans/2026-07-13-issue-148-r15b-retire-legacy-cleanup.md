# R15-B Legacy Cleanup Physical Retirement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Physically remove the legacy cleanup runtime, its five admin routes, runtime database markers, and two obsolete tables.

**Architecture:** Preserve only the stable disabled response needed by still-addressable retired actions as a pure shared contract with no database writes. Delete the complete legacy cleanup module and router, then use migration `0105` to drop its registry/audit tables and update lifecycle/ownership inventories to make reintroduction fail CI.

**Tech Stack:** Python 3.10, FastAPI route registry, Alembic/PostgreSQL, YAML/JSON architecture manifests, pytest.

---

### Task 1: Replace database marker calls with a stateless retired contract

**Files:**
- Create: `aicrm_next/platform/shared/retired_contracts.py`
- Create: `tests/test_retired_runtime_contract.py`
- Modify: `aicrm_next/platform/admin_jobs/application.py`
- Modify: `aicrm_next/platform/admin_jobs/notification_settings.py`
- Modify: `aicrm_next/commerce/admin_refunds.py`
- Modify: `aicrm_next/commerce/external_push_admin.py`
- Modify: `aicrm_next/platform/external_push/service.py`
- Modify: `aicrm_next/owner_migration/application.py`

**Steps:** Write failing tests for the stable disabled payload and zero legacy-cleanup imports; implement the pure contract; delete all best-effort marker helpers/calls; run caller contract tests.

### Task 2: Delete the runtime module and route surface

**Files:**
- Delete: `aicrm_next/platform/platform_foundation/legacy_cleanup/`
- Delete: `tests/test_legacy_webhook_cleanup.py`
- Modify: `aicrm_next/router_registry.py`
- Modify: `aicrm_next/fixture_reset_registry.py`
- Modify: auth capability profile and route inventories

**Steps:** Add assertions that the module and five route paths are absent; delete router/reset/capability registration; regenerate route and runtime contract inventories; run route-policy and auth gates.

### Task 3: Drop obsolete tables with migration 0105

**Files:**
- Create: `migrations/versions/0105_drop_legacy_cleanup_tables.py`
- Modify: `docs/architecture/data_table_lifecycle_manifest.yml`
- Modify: `docs/architecture/repository_ownership.yml`
- Modify: migration/schema tests

**Steps:** Add a migration contract test; drop audit then registry tables; provide schema-only downgrade recreation without restoring runtime; mark both tables retired/physically removed; remove repository ownership; run fresh-install and upgrade suites.

### Task 4: Add permanent retired-reference scanner

**Files:**
- Create: `tools/check_retired_runtime_references.py`
- Create: `docs/architecture/retired_runtime_registry.yml`
- Create: `tests/test_retired_runtime_reference_scanner.py`
- Modify: `scripts/ci/run_architecture_gates.sh`
- Modify: `docs/ci/test_scope_manifest.yml`
- Modify: `tests/test_select_test_scope.py`

**Steps:** Scan active runtime, deployment, workflow, and operational scripts for forbidden module/route/table/unit/env references; keep the registry and historical migrations as explicit evidence exclusions; inject one forbidden reference in a temp tree to prove fail-closed behavior; wire into full architecture CI.

### Task 5: Verify and publish

**Steps:** Run focused caller tests, migration chain/fresh install/upgrade, full architecture gates, real changed-files selector, and secret/PII scan. Rebase after #147, open a Chinese PR closing #148, wait for full CI, merge, and verify the exact test deployment.
