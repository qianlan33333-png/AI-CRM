# Lead QR Copy Configuration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow each standard product, questionnaire, and service-period product to independently configure the main title and subtitle shown above its bound channel QR code, while preserving existing copy when both fields are blank.

**Architecture:** Store `lead_qr_title` and `lead_qr_subtitle` on the owning questionnaire or trade product, never on the reusable channel-code entity. Normalize the two optional strings at the application/domain boundary and project them through the existing `completion_action_with_lead_qr` contract so all public surfaces consume the same payload shape. Service-period products keep their independent values on their dedicated hidden trade product.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL/Alembic, Jinja2, vanilla JavaScript, pytest, Node frontend contract tests.

---

### Task 1: Shared copy contract and schema

**Files:**
- Modify: `aicrm_next/platform/navigation_target/domain.py`
- Modify: `aicrm_next/platform/navigation_target/__init__.py`
- Create: `migrations/versions/0168_lead_qr_copy_config.py`
- Modify: `migrations/baselines/0001_post_legacy.sql`
- Test: `tests/unit/test_extension_rules.py`

**Steps:**

1. Add a failing unit test for trimming, blank preservation, maximum lengths, and completion-action projection.
2. Run `pytest -q tests/unit/test_extension_rules.py` and confirm the new assertions fail.
3. Add `normalize_lead_qr_copy()` with a 40-character title limit and 100-character subtitle limit.
4. Add nullable-compatible, non-null text columns with empty-string defaults to `wechat_pay_products` and `questionnaires`.
5. Run the focused unit test and migration-head tests.

### Task 2: Standard product and service-period persistence

**Files:**
- Modify: `aicrm_next/extensions/commerce/commerce/dto.py`
- Modify: `aicrm_next/extensions/commerce/commerce/domain.py`
- Modify: `aicrm_next/extensions/commerce/commerce/application.py`
- Modify: `aicrm_next/extensions/commerce/commerce/repo.py`
- Modify: `aicrm_next/extensions/commerce/service_period/dto.py`
- Modify: `aicrm_next/extensions/commerce/service_period/application.py`
- Test: `tests/unit/test_extension_rules.py`

**Steps:**

1. Add failing tests that save different copy on a standard product and service-period product.
2. Pass both fields through Pydantic DTOs and the existing commerce upsert command.
3. Persist and serialize both fields in the in-memory and PostgreSQL repositories.
4. Ensure service-period create/update preserves the fields on its own hidden trade product.
5. Run focused unit tests.

### Task 3: Questionnaire persistence and operations UI

**Files:**
- Modify: `aicrm_next/extensions/forms/questionnaire/domain.py`
- Modify: `aicrm_next/extensions/forms/questionnaire/operations.py`
- Modify: `aicrm_next/extensions/forms/questionnaire/repo.py`
- Modify: `aicrm_next/extensions/forms/questionnaire/repo_memory.py`
- Modify: `aicrm_next/extensions/forms/questionnaire/repo_support.py`
- Modify: `aicrm_next/extensions/forms/questionnaire/templates/admin_console/questionnaire_operations.html`
- Modify: `aicrm_next/extensions/forms/questionnaire/static/questionnaire_operations.js`
- Test: `tests/high_risk/test_questionnaire.py`

**Steps:**

1. Add failing tests for save, readback, blank compatibility, and completion payload projection.
2. Add two optional inputs beside the existing channel selector on the level-2 operations page.
3. Save and read the fields through `QuestionnaireOperationsService` and both repositories.
4. Include the fields in the resolved `lead_qr` completion action.
5. Run focused questionnaire tests.

### Task 4: Product and service-period admin UI

**Files:**
- Modify: `aicrm_next/extensions/commerce/commerce/templates/wechat_products.html`
- Modify: `aicrm_next/extensions/commerce/service_period/templates/service_period_products.html`
- Test: `tests/frontend/page_wiring.test.mjs`

**Steps:**

1. Add failing page-wiring assertions for both inputs and request payload fields.
2. Reuse each page's existing form grid inside the channel-QR configuration area.
3. Populate saved values and submit trimmed values without adding a new API wrapper.
4. Run `node --test tests/frontend/page_wiring.test.mjs`.

### Task 5: Public rendering and compatibility

**Files:**
- Modify: `aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py`
- Modify: `aicrm_next/extensions/commerce/public_product/service.py`
- Modify: `aicrm_next/extensions/commerce/service_period/application.py`
- Modify: `aicrm_next/extensions/commerce/service_period/public.py`
- Modify: `aicrm_next/extensions/forms/questionnaire/static/questionnaire_completion_action.js`
- Modify: `aicrm_next/app/admin_console/templates/questionnaire_h5_page.html`
- Test: `tests/unit/test_extension_rules.py`
- Test: `tests/high_risk/test_questionnaire.py`
- Test: `tests/frontend/page_wiring.test.mjs`

**Steps:**

1. Add failing assertions for configured and blank copy.
2. Project configured copy inside `lead_qr`; omit semantic defaults from storage.
3. For blank product/service-period values, retain `报名成功` and `扫码添加企微领取后续资料`.
4. For blank questionnaire values, retain channel-name-or-`扫码继续` title and `长按识别二维码，继续后续服务` subtitle.
5. Run focused backend and frontend tests.

### Task 6: Verification and delivery

**Files:**
- Modify if required: `docs/ci/test_scope_manifest.yml`
- Verify: `docs/architecture/route_ownership_manifest.yml`

**Steps:**

1. Run migration-head, architecture, focused unit, high-risk questionnaire, and frontend test suites.
2. Run the repository CI selector/checker for the changed files.
3. Review generated HTML and JavaScript for duplicate titles, overloaded pages, and escaped output.
4. Commit the isolated branch, push it, and create a Chinese PR with Summary, Architecture boundary, Safety / non-goals, Verification, Risk / rollback, and Next action.
