# Required Mobile Validation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Require every configured questionnaire mobile answer and every `require_mobile` order checkout to contain a valid 11-digit mainland China mobile number.

**Architecture:** Add a small shared domain utility that normalizes separators and accepts only `1[3-9]` followed by nine digits. Questionnaire domain validation rejects invalid non-empty mobile answers before persistence; commerce and public-product command paths reject invalid required mobiles before creating an order. Existing form pages keep their current information architecture and error surfaces while adding immediate questionnaire mobile feedback.

**Tech Stack:** FastAPI, Pydantic, Jinja2/vanilla JavaScript, pytest, ruff.

---

### Task 1: Shared mobile rule and questionnaire backend validation

**Files:**
- Create: `aicrm_next/platform/shared/mobile.py`
- Modify: `aicrm_next/questionnaire/domain.py`
- Modify: `aicrm_next/questionnaire/h5_write.py`
- Test: `tests/test_questionnaire_h5_submit_validation.py`
- Test: `tests/test_questionnaire_mobile_normalization.py`

**Step 1: Write the failing tests**

Add a required mobile-question fixture submission with `186109474111` and assert HTTP 400 with no submission write. Add normalization assertions for a valid spaced 11-digit value and rejection of 10/12-digit values.

**Step 2: Run tests to verify failure**

Run: `.venv/bin/pytest tests/test_questionnaire_h5_submit_validation.py tests/test_questionnaire_mobile_normalization.py -q`

Expected: the 12-digit questionnaire submission is currently accepted or reaches the binding stage.

**Step 3: Implement the minimal domain rule**

Create:

```python
def normalize_mainland_mobile(value: object) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    return digits if re.fullmatch(r"1[3-9]\d{9}", digits) else ""
```

Call it from questionnaire validation for every non-empty `mobile` question, and use the normalized answer in the H5 identity/binding payload.

**Step 4: Run tests to verify they pass**

Run the same focused pytest command and expect all tests to pass.

### Task 2: Questionnaire frontend validation

**Files:**
- Modify: `aicrm_next/frontend_compat/templates/questionnaire_h5_page.html`
- Test: `tests/test_questionnaire_h5_submit_validation.py`

**Step 1: Write the failing frontend contract assertion**

Assert that both server-rendered and one-by-one mobile inputs use `maxlength=11`, normalize to digits, and show `请输入11位有效手机号。` before submission.

**Step 2: Implement the existing-page pattern**

Keep the current page, state message, and one-by-one flow. Add no components or API wrappers. Limit the field to 11 digits and extend `validateQuestion()` for `question.type === 'mobile'`.

**Step 3: Run the focused tests**

Run: `.venv/bin/pytest tests/test_questionnaire_h5_submit_validation.py tests/test_questionnaire_h5_submit_commands.py -q`

### Task 3: Required-mobile order validation

**Files:**
- Modify: `aicrm_next/public_product/sidebar_order_context.py`
- Modify: `aicrm_next/public_product/h5_wechat_pay.py`
- Modify: `aicrm_next/commerce/application.py`
- Test: `tests/test_public_product_sidebar_order_context.py`
- Test: `tests/test_service_period_h5_payment.py`
- Test: `tests/test_checkout_api_contract.py`

**Step 1: Write failing order tests**

Cover 10/12-digit required mobiles for the public WeChat Pay route, service-period route, and generic WeChat/Alipay checkout commands. Assert HTTP 400 and that no order/payment adapter execution occurs.

**Step 2: Implement command-boundary validation**

Normalize payload and stored binding values through the shared rule. When `require_mobile` is true and no valid normalized value exists, return/raise the existing 400-level validation error before `_insert_order()` or `repo.create_order()`.

**Step 3: Run order tests**

Run: `.venv/bin/pytest tests/test_public_product_sidebar_order_context.py tests/test_service_period_h5_payment.py tests/test_checkout_api_contract.py tests/test_public_product_frontend_contract.py -q`

### Task 4: Verification and delivery

**Files:**
- Verify all touched files.

**Step 1: Run focused regression tests**

Run the questionnaire and order test bundles above.

**Step 2: Run static checks**

Run: `.venv/bin/ruff check aicrm_next/platform/shared/mobile.py aicrm_next/questionnaire/domain.py aicrm_next/questionnaire/h5_write.py aicrm_next/public_product/sidebar_order_context.py aicrm_next/public_product/h5_wechat_pay.py aicrm_next/commerce/application.py tests/test_questionnaire_h5_submit_validation.py tests/test_questionnaire_mobile_normalization.py tests/test_public_product_sidebar_order_context.py tests/test_service_period_h5_payment.py tests/test_checkout_api_contract.py`

Run: `git diff --check`

**Step 3: Review frontend requirements**

Confirm no new component/API wrapper, no title duplication, no page hierarchy change, and current state/error presentation is reused.

**Step 4: Commit and publish**

Commit the scoped change, push `codex/validate-required-mobile-fields`, and create a Chinese PR with Summary, Architecture boundary, Safety/non-goals, Verification, Risk/rollback, and Next action.
