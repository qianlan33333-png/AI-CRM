# Group Invite Card All Surfaces Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add one reusable WeCom group-invite card material that can be selected and sent by broadcasts, private broadcasts, AI assistants, automation operations, and channel welcome messages.

**Architecture:** Extend the Next-native media library and standard send-content package with a `group_invite` asset backed by `group_invite_library`. Resolve the asset at the existing material boundary into an official WeCom `link` attachment, while keeping every business surface on its current application and queue owner.

**Tech Stack:** FastAPI, Pydantic v2, PostgreSQL/Alembic, vanilla JavaScript/Jinja, pytest.

---

### Task 1: Database and group-invite media contract

**Files:**
- Create: `migrations/versions/0117_group_invite_cards.py`
- Modify: `aicrm_next/engagement/media_library/dto.py`
- Modify: `aicrm_next/engagement/media_library/repo.py`
- Modify: `aicrm_next/engagement/media_library/postgres_repo.py`
- Test: `tests/test_group_invite_library.py`

**Steps:**

1. Write failing repository and validation tests for a reusable card with a valid `https://work.weixin.qq.com/gm/...` URL.
2. Run `pytest tests/test_group_invite_library.py -q` and confirm the new type is unsupported.
3. Add the migration, DTO validation, fixture repository support, and PostgreSQL CRUD.
4. Re-run the test and confirm it passes.

### Task 2: Admin API and reusable management page

**Files:**
- Modify: `aicrm_next/engagement/media_library/api.py`
- Modify: `aicrm_next/engagement/media_library/admin_pages.py`
- Modify: `aicrm_next/admin_shell/navigation.py`
- Create: `aicrm_next/frontend_compat/templates/admin_console/group_invite_library.html`
- Test: `tests/test_group_invite_library.py`
- Test: `tests/test_media_library_admin_pages_native.py`

**Steps:**

1. Add failing API/page contracts for list, create, update, delete, navigation and required assets.
2. Implement Next-native CRUD routes and the level-2 library page by reusing existing media-library layout and request conventions.
3. Run the focused API/page tests.

### Task 3: Standard send-content package and shared picker

**Files:**
- Modify: `aicrm_next/engagement/send_content/dto.py`
- Modify: `aicrm_next/engagement/send_content/application.py`
- Modify: `aicrm_next/engagement/send_content/repo.py`
- Modify: `aicrm_next/engagement/send_content/postgres_repo.py`
- Modify: `aicrm_next/frontend_compat/static/admin_console/material_picker.js`
- Modify: `aicrm_next/frontend_compat/static/admin_console/send_content_composer.js`
- Modify: `aicrm_next/frontend_compat/static/admin_console/send_content_composer.css`
- Test: `tests/test_send_content_next_native.py`
- Test: `tests/test_next_material_picker_api.py`
- Test: `tests/test_next_send_content_frontend_contract.py`

**Steps:**

1. Add failing tests that normalize, list, preview, validate and render `group_invite_library_ids` with a maximum of one card.
2. Extend the shared content package, unified asset read model and picker.
3. Add the shared “+群邀请” button and group-invite preview card.
4. Run focused backend and frontend contract tests.

### Task 4: WeCom link payload and all send queues

**Files:**
- Modify: `aicrm_next/automation/automation_engine/group_ops/material_resolver.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/message_content.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/domain.py`
- Modify: `aicrm_next/automation/background_jobs/broadcast_queue_worker.py`
- Modify: `aicrm_next/external_effect_composition.py`
- Modify: `aicrm_next/cloud_orchestrator/repository.py`
- Test: `tests/test_group_ops_material_resolver.py`
- Test: `tests/test_group_ops_domain.py`
- Test: `tests/test_broadcast_queue_worker.py`

**Steps:**

1. Add failing tests for resolving a group-invite material into a canonical `link` attachment.
2. Allow and validate only complete link payloads.
3. Preserve the new IDs through cloud/private job hydration and resolve before WeCom dispatch.
4. Run the focused send-path tests.

### Task 5: Welcome-message configuration and resolution

**Files:**
- Modify: `aicrm_next/automation/automation_engine/channels_repo.py`
- Modify: `aicrm_next/automation/automation_engine/channels_api.py`
- Modify: `aicrm_next/channels/channel_entry/repo.py`
- Modify: `aicrm_next/channels/channel_entry/application.py`
- Modify: `aicrm_next/external_effect_composition.py`
- Modify: `aicrm_next/automation/automation_engine/templates/admin_console/channel_code_form.html`
- Modify: `aicrm_next/automation/automation_engine/static/admin_console/channel_admission_pages.js`
- Test: `tests/test_next_channel_welcome_attachments.py`
- Test: `tests/test_channel_multi_staff_backend.py`
- Test: `tests/test_channel_multi_staff_frontend_contract.py`

**Steps:**

1. Add failing tests that persist and resolve `welcome_group_invite_library_ids`.
2. Add the JSONB channel field and map it to the standard content composer.
3. Resolve welcome link materials before `send_welcome_msg`.
4. Run the focused welcome tests.

### Task 6: Preserve the field across AI assistant and automation UIs

**Files:**
- Modify: `aicrm_next/automation_agents/templates/admin_console/automation_agent_edit.html`
- Modify: `aicrm_next/automation_agents/templates/admin_console/automation_agent_list.html`
- Modify: `aicrm_next/automation/automation_engine/group_ops/static/admin_console/group_ops.js`
- Modify: `aicrm_next/frontend_compat/templates/admin_console/cloud_campaigns_workspace.html`
- Modify: `aicrm_next/frontend_compat/static/admin_console/cloud_plan_review.js`
- Modify: `aicrm_next/frontend_compat/static/admin_console/user_ops_batch_send_modal.js`
- Test: `tests/test_next_send_content_frontend_contract.py`
- Test: `tests/test_group_ops_frontend_contract.py`
- Test: `tests/test_automation_agents_admin_pages.py`

**Steps:**

1. Add contract assertions for every named business surface.
2. Update local normalizers and summaries so they do not drop the new field.
3. Run the focused frontend contracts.

### Task 7: Full verification and delivery

**Files:**
- Modify: `docs/architecture/media_library_route_inventory.md`
- Modify: `docs/plans/2026-07-15-group-invite-card-design.md` if verification changes the design.

**Steps:**

1. Run all focused tests from Tasks 1-6.
2. Run architecture/import/route checkers required by the repository.
3. Inspect the rendered management page and shared composer for layout regressions.
4. Review `git diff --check`, migration head, and repository status.
5. Commit, push `codex/group-invite-card-all-surfaces`, open a Chinese PR with Summary, Architecture boundary, Safety/non-goals, Verification, Risk/rollback and Next action.
