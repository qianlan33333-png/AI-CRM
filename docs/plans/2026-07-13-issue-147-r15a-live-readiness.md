# R15-A Live Runtime Readiness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace synthetic in-process production probes with a fail-closed HTTP verifier for the exact deployed web and worker release.

**Architecture:** Delete the two obsolete tools that boot a fake production app and infer readiness from non-404 responses. Add one read-only verifier that calls the deployed public liveness/readiness endpoints and the authenticated runtime route map, requires an expected 40-character SHA, and returns structured evidence without printing the service token. Remove stale 5013 fallback projections instead of carrying compatibility telemetry forward.

**Tech Stack:** Python 3.10, `requests`, FastAPI runtime contracts, pytest, GitHub Actions test-scope selection.

---

### Task 1: Lock the fail-closed verifier contract

**Files:**
- Create: `tests/test_live_runtime_readiness.py`
- Create: `tools/check_live_runtime_readiness.py`

**Step 1:** Write tests with a fake HTTP session for missing configuration, liveness failure, readiness 503, release mismatch, missing service token, route-map 401, worker SHA mismatch, success, and token redaction.

**Step 2:** Run `.venv/bin/python -m pytest -q tests/test_live_runtime_readiness.py` and verify the new module import fails.

**Step 3:** Implement `run_check` with required `base_url`, `expected_sha`, and token read from an environment variable. Validate the SHA before network access; require HTTP 200 and JSON contracts from `/health`, `/api/system/health`, and `/api/system/runtime-route-map`.

**Step 4:** Run the focused test and require all cases to pass.

### Task 2: Physically remove obsolete synthetic diagnostics

**Files:**
- Delete: `tools/check_next_production_runtime_gaps.py`
- Delete: `tools/check_next_production_cutover_readiness.py`
- Delete: `tests/test_retired_runtime_gap_timer_report.py`
- Delete: `tests/test_retired_timer_readiness_cleanup.py`
- Modify: repository references selected by `rg`

**Step 1:** Add a contract test asserting the obsolete tool paths no longer exist and fake production probe env keys are absent from active diagnostic tools.

**Step 2:** Delete the tools/tests and update direct imports or documentation references.

**Step 3:** Run the focused diagnostic and repo-hygiene suites.

### Task 3: Remove stale callback fallback state

**Files:**
- Modify: `aicrm_next/insights/admin_read_model/projections.py`
- Modify: affected admin read-model tests

**Step 1:** Add/adjust a test proving admin projections no longer report retained 5013 fallback state.

**Step 2:** Remove the stale callback fallback card/status while preserving current operational cards.

**Step 3:** Run the admin read-model and page contract suites.

### Task 4: Wire permanent CI coverage and inventories

**Files:**
- Modify: `docs/ci/test_scope_manifest.yml`
- Modify: `tests/test_select_test_scope.py`
- Regenerate: `docs/architecture/runtime_contract_inventory.json` only if runtime contract content changes

**Step 1:** Add a permanent high-risk scope for the live verifier and deleted diagnostic paths.

**Step 2:** Run the real changed-files selector and verify no unmatched files, PostgreSQL/full CI selection, and full architecture gates.

**Step 3:** Run `bash scripts/ci/run_architecture_gates.sh` and the complete focused suite.

### Task 5: Commit and publish

**Files:** all files above.

**Step 1:** Run `git diff --check`, secret/PII review, and confirm no external writes are possible from the verifier.

**Step 2:** Commit the implementation, rebase onto the final R14-C main SHA, push, open a Chinese PR closing #147, wait for full CI, merge, and verify the exact test deployment.
