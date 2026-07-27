# R15-C Critical Read Path Performance Baselines Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Establish repeatable PostgreSQL dataset, query-count, pagination, plan, and p50/p95 regression gates for Customer List, Sidebar Workbench, Questionnaire Admin, and Admin Jobs.

**Architecture:** Keep performance evidence outside request handlers in a read-only benchmark runner that executes the existing Next-owned repository/application paths against a disposable migrated PostgreSQL database. Version the anonymous dataset and latency budgets as reviewed JSON, fail closed on more than 110% of the reviewed p95, and enforce deterministic query-count, page-size, and query-plan rules separately so correctness does not depend only on noisy wall-clock timing.

**Tech Stack:** Python 3.10, PostgreSQL 16, SQLAlchemy/psycopg instrumentation, Alembic, pytest, GitHub Actions.

---

### Task 1: Define reviewed baseline contracts and failure semantics

**Files:**
- Create: `docs/performance/critical_read_path_baselines.json`
- Create: `aicrm_next/platform/platform_foundation/performance_contracts.py`
- Create: `tests/test_critical_read_performance_contracts.py`

**Steps:** Add four route-owned profiles with fixed dataset sizes, sample counts, maximum query counts, page limits, reviewed p95 values, 1.10 regression factor, and prohibited large sequential scans. Validate duplicate/missing profiles, invalid budgets, and explicit baseline updates; prove an injected query-count, pagination, plan, or p95 regression fails.

### Task 2: Build a deterministic PostgreSQL benchmark runner

**Files:**
- Create: `tools/check_critical_read_performance.py`
- Create: `tests/test_critical_read_performance_runner.py`

**Steps:** Seed anonymous fixed-size rows using PostgreSQL `generate_series`; warm each path; execute multiple samples through existing repositories/application read models; count SQL statements; collect p50/p95; capture `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` evidence; reject unbounded results and large unexpected sequential scans; emit a secret-free JSON report and always clean the disposable data.

### Task 3: Remove confirmed hot-path scans and add necessary indexes

**Files:**
- Modify: `aicrm_next/customer_read_model/repo.py`
- Modify: `aicrm_next/customer_read_model/sidebar_v2.py`
- Modify: `aicrm_next/questionnaire/repo.py`
- Modify: `aicrm_next/platform/admin_jobs/repository.py`
- Create: `migrations/versions/0106_critical_read_path_indexes.py`
- Modify: lifecycle/schema/index tests as required

**Steps:** Run the benchmark against the migrated database, inspect plan evidence, replace only confirmed N+1/full scans, clamp all four public list limits, and add only indexes proven necessary by the fixed dataset. Verify fresh install and upgrade paths.

### Task 4: Wire nightly and PR regression gates

**Files:**
- Modify: `.github/workflows/full-regression.yml`
- Modify: `docs/ci/test_scope_manifest.yml`
- Modify: `tests/test_select_test_scope.py`
- Modify: architecture/runtime inventories if new active files require them

**Steps:** Run the performance contract in the PostgreSQL governance job for full/high-risk PRs and the existing nightly schedule; upload the JSON performance report as evidence; keep ordinary fast paths unchanged; ensure benchmark/config changes force the full PostgreSQL path.

### Task 5: Verify and publish

**Steps:** Run contract unit tests, PostgreSQL benchmark tests, migration chain/fresh install/upgrade, full architecture gates, changed-files selector, and secret/PII scan. Rebase after #148, open a Chinese PR closing #149, wait for full CI, merge, and verify the exact test deployment.
