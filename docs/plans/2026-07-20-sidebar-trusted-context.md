# Sidebar Trusted Context Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the sidebar authorization dependency on synchronized customer-owner relationships while preserving OAuth identity and session-bound signed grants.

**Architecture:** Keep the existing Next-native OAuth, signed state, HttpOnly viewer session, and short-lived sidebar grant. Delete only the local owner-candidate lookup and rejection branches from `aicrm_next.identity_contact.sidebar_jssdk`; no frontend component, provider relationship call, legacy fallback, or synchronization-job change is introduced.

**Tech Stack:** Python, FastAPI, Starlette TestClient, pytest, itsdangerous signed sessions.

---

### Task 1: Lock the trusted-session behavior with tests

**Files:**
- Modify: `tests/test_sidebar_jssdk_adapter.py:165-413`

**Step 1: Write the failing tests**

- Change the former out-of-owner callback test to expect a successful redirect and viewer Cookie.
- Change the former out-of-owner JSSDK test to expect `sidebar_owner_token_status == "issued"`.
- Assert the application owner-candidate query can raise if called, proving the new flow does not depend on it.
- Remove assertions for `owner_candidates_count`.

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_sidebar_jssdk_adapter.py`

Expected: the trusted-session cases fail because the current implementation still returns `viewer_not_in_contact_owner_scope`.

### Task 2: Remove the relationship gate

**Files:**
- Modify: `aicrm_next/identity_contact/sidebar_jssdk.py:39,211-216,244-360`

**Step 1: Implement the minimal code**

- Remove the `ListExternalContactOwnerCandidatesQuery` import and helper.
- Remove owner-candidate checks from OAuth callback and `_with_sidebar_owner_context`.
- Keep viewer session presence and exact external-user match checks.
- Remove the obsolete `owner_candidates_count` response field and function parameter.

**Step 2: Run focused tests**

Run: `.venv/bin/pytest -q tests/test_sidebar_jssdk_adapter.py tests/test_sidebar_owner_context_security.py tests/test_route_policy_enforcement.py`

Expected: all tests pass.

### Task 3: Verify frontend and architecture contracts

**Files:**
- Verify only: `aicrm_next/frontend_compat/templates/sidebar_customer_workbench.html`
- Verify only: `aicrm_next/frontend_compat/static/sidebar_workbench/sidebar_workbench.js`

**Step 1: Run sidebar contract tests**

Run: `.venv/bin/pytest -q tests/test_sidebar_jssdk_frontend_contract.py tests/test_sidebar_jssdk_inventory.py tests/test_sidebar_jssdk_no_real_external_calls.py tests/test_next_sidebar_workbench_routes.py`

Expected: all tests pass without frontend changes.

**Step 2: Run architecture checks and diff validation**

Run: `scripts/ci/run_architecture_gates.sh`

Run: `git diff --check`

Expected: both commands succeed.

### Task 4: Publish and promote

**Files:**
- Commit the implementation, tests, generated runtime inventory, and two plan documents.

**Step 1: Commit and push**

Run: `git add aicrm_next/identity_contact/sidebar_jssdk.py tests/test_sidebar_jssdk_adapter.py docs/architecture/runtime_contract_inventory.json docs/plans/2026-07-20-sidebar-trusted-context-design.md docs/plans/2026-07-20-sidebar-trusted-context.md`

Run: `git commit -m "简化侧边栏可信会话授权"`

Run: `git push -u origin codex/sidebar-trusted-context`

Expected: branch is available on GitHub.

**Step 2: Create, review, and merge the PR**

Create a Chinese PR body with Summary, Architecture boundary, Safety / non-goals, Verification, Risk / rollback, and Next action. Wait for required checks, then merge using the repository-allowed merge method.

**Step 3: Verify production**

Follow the repository production promotion workflow for the merged `main` SHA. Verify `/health`, `/api/system/health`, `x-aicrm-release-sha`, and a real OAuth/JSSDK grant for the affected employee/customer without changing customer data.
