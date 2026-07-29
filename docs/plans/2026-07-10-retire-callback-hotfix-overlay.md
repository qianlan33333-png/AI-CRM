# Retire Callback Hotfix Overlay Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Permanently stop the June 28 callback hotfix overlay from replacing current channel-entry source and deployment checks before production service starts.

**Architecture:** Keep the cleanup inside the manifest-driven production runtime manager so the retired drop-ins are explicit, testable, idempotent, and rechecked during every deploy. Run the cleanup immediately after the deployment resets to `origin/main`, before migrations or any service restart, then retain the existing 5002 smoke gate that requires `time_sensitive_inline_enabled=true`.

**Tech Stack:** Python 3, systemd, GitHub Actions YAML, pytest.

---

### Task 1: Declare and validate retired callback overlay drop-ins

**Files:**
- Modify: `deploy/production_runtime_units.json`
- Modify: `scripts/ops/manage_production_runtime_units.py`
- Test: `tests/test_runtime_units_autostart.py`

**Step 1: Write the failing test**

Add assertions that the manifest lists the exact three `10-aicrm-callback-hotfix-runtime.conf` drop-ins and that the new retirement phase removes them, reloads systemd, and verifies absence.

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_runtime_units_autostart.py`

Expected: FAIL because the manifest and `retire-legacy-overlays` phase do not exist.

**Step 3: Write minimal implementation**

Add a validated `retired_dropins` manifest section, a `RetiredDropIn` parser, an idempotent removal phase using `sudo rm -f`, and absence checks using `sudo test ! -e`.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_runtime_units_autostart.py`

Expected: PASS.

### Task 2: Run retirement before any production restart

**Files:**
- Modify: `.github/workflows/deploy.yml`
- Test: `tests/test_deploy_workflow_contract.py`

**Step 1: Write the failing test**

Assert that `retire-legacy-overlays` runs after the clean reset and before migration, 5001 restart, and runtime-unit installation.

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_deploy_workflow_contract.py`

Expected: FAIL because the workflow does not invoke the retirement phase.

**Step 3: Write minimal implementation**

Invoke the runtime manager's retirement phase immediately after writing `.release-sha`.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_deploy_workflow_contract.py`

Expected: PASS.

### Task 3: Verify deployment and production recovery

**Files:**
- Verify: `scripts/ops/check_wecom_callback_deploy_smoke.py`
- Verify: `aicrm_next/channels/channel_entry/inbox.py`
- Verify: `aicrm_next/channels/channel_entry/ingress_app.py`

**Step 1: Run focused regression tests**

Run: `.venv/bin/python -m pytest -q tests/test_runtime_units_autostart.py tests/test_deploy_workflow_contract.py tests/test_wecom_callback_deploy_smoke.py tests/test_wecom_callback_inbox.py tests/test_wecom_callback_ingress_runtime.py`

Expected: PASS.

**Step 2: Run repository checks**

Run: `git diff --check`

Expected: no output.

**Step 3: Publish and merge**

Commit the scoped changes, push `codex/retire-callback-hotfix-overlay`, create a Chinese PR, wait for required checks, and merge.

**Step 4: Verify production**

Confirm the deployment removes all retired drop-ins, the production worktree is clean apart from `.release-sha`, 5002 health reports `time_sensitive_inline_enabled=true`, and a fresh `State + WelcomeCode` callback is processed inline rather than by the minute worker.

**Rollback:** Restore the archived drop-in backups under `/home/ubuntu/aicrm-hotfix/` only if the current Next callback runtime cannot start, then roll back to the previous release. Do not restore the retired legacy runtime or nginx quick-ACK path.
