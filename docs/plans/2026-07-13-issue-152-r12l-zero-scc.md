# R12-L Zero Runtime SCC Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the remaining 16-context runtime import SCC and make cyclic runtime context imports permanently impossible.

**Architecture:** Preserve the modular monolith and existing package locations, but move concrete cross-context assembly to top-level composition modules that the import graph already treats as the composition root. Platform and integration packages expose neutral protocols/registries only; business contexts depend downward on contracts/adapters, while `aicrm_next/main.py` installs concrete routes, event consumers, effect adapters, identity queries, and presentation extensions per app container.

**Tech Stack:** Python 3.10, FastAPI app factory/lifespan, typed protocols/callables, pytest, AST import graph gate.

---

### Task 1: Freeze the exact reverse-edge set and fail on hidden workarounds

**Files:**
- Create: `tests/test_zero_runtime_import_scc.py`
- Modify: `tools/check_import_graph.py`
- Modify: `docs/architecture/import_graph_baseline.yml`

**Steps:** Record the current 16-context SCC and its reverse-edge evidence; add tests that reject platform→business, integration_gateway→business, non-literal/dynamic imports, and composition callbacks installed outside the app factory; keep the current baseline until the final task so intermediate work can run.

### Task 2: Invert platform-foundation concrete business dependencies

**Files:**
- Modify: `aicrm_next/platform/platform_foundation/external_effects/`
- Modify: `aicrm_next/platform/platform_foundation/internal_events/`
- Modify: `aicrm_next/platform/platform_foundation/webhook_inbox/`
- Modify: `aicrm_next/platform/platform_foundation/push_center/`
- Modify: `aicrm_next/platform/platform_foundation/readiness.py`
- Create/modify top-level composition modules under `aicrm_next/*.py`
- Modify: `aicrm_next/main.py`

**Steps:** Replace imports of admin jobs, shell, channel entry, cloud, commerce, external push, identity, integration gateway, questionnaire, and service period with neutral ports/registries; construct and install implementations from app-scoped composition; prove two app instances cannot share registrations.

### Task 3: Invert integration-gateway business dependencies

**Files:**
- Modify: `aicrm_next/channels/integration_gateway/dispatch.py`
- Modify: `aicrm_next/channels/integration_gateway/questionnaire_adapters.py`
- Create/modify top-level customer/identity/questionnaire composition modules
- Modify relevant contract tests

**Steps:** Move customer/identity dispatch and questionnaire tag/identity mutations behind injected application ports; retain provider protocol code inside integration gateway; preserve every existing error/result contract and real-effect gate.

### Task 4: Remove presentation/auth and direct business bidirectional edges

**Files:**
- Modify: `aicrm_next/platform/admin_auth/`, `aicrm_next/admin_shell/`
- Modify: `aicrm_next/identity_contact/application.py`
- Modify: `aicrm_next/commerce/external_orders.py`, `order_reconciliation.py`
- Modify: `aicrm_next/platform/external_push/repo.py`
- Modify: `aicrm_next/customer_read_model/sidebar_v2.py`
- Modify: `aicrm_next/media_library/repo.py`
- Modify related service-period/public-product route composition

**Steps:** Move action-token/navigation and questionnaire result access to presentation composition; inject customer/identity queries into commerce and identity façades; relocate neutral product-code/material contracts; remove commerce↔customer, commerce↔public, commerce↔external-push and customer↔identity cycles without changing route or SQL behavior.

### Task 5: Set the permanent zero-cycle gate and verify

**Files:**
- Modify: `docs/architecture/import_graph_baseline.yml`
- Modify: `docs/architecture/runtime_contract_inventory.json`
- Modify: `docs/ci/test_scope_manifest.yml`
- Modify: `tests/test_import_graph_guard.py`, `tests/test_select_test_scope.py`

**Steps:** Set `max_cyclic_contexts: 0`, remove the historical SCC allowlist, require `cyclic_component_count=0`, regenerate inventories, run app multi-instance isolation, full architecture gates, complete PostgreSQL and frontend regression, then publish one structural PR closing #152. Rollback is the previous release; no compatibility import or dynamic fallback is retained.
