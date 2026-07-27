# Group Ops Token Broadcast API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add one token-authenticated AI-CRM Next API that can synchronously send text-only, uploaded-image, lesson-card, or combined messages to the active group-ops plan without an admin browser session.

**Architecture:** Add a Next-native group-ops application command behind a non-admin `/api/automation/group-ops/broadcast` route. The API validates `AUTOMATION_INTERNAL_API_TOKEN`, normalizes JSON or multipart input, and delegates to the command; the command reuses the existing media upload, group-ops queue, and exact external-effect job dispatch boundaries. It never grants token access to general `/api/admin/*` routes.

**Tech Stack:** FastAPI, Pydantic, existing group-ops repositories, Cloud Orchestrator media upload command, External Effect Queue, pytest.

---

### Task 1: Define the request and authentication contract

**Files:**
- Create: `aicrm_next/automation/automation_engine/group_ops/broadcast.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/dto.py`
- Test: `tests/test_group_ops_token_broadcast_api.py`

**Step 1: Write failing tests**

Cover missing/invalid Bearer token, missing idempotency key, empty content, malformed `card_path`, image count/size/type limits, and secret-free errors.

**Step 2: Run tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_group_ops_token_broadcast_api.py -q`

Expected: failures because the route and command do not exist.

**Step 3: Implement minimal validation**

Add a request model supporting:

```json
{
  "text": "message text",
  "card_path": "pages/article/article?lesson_id=<uuid>&from=learn",
  "card_title": "optional title",
  "image_media_ids": ["optional existing media ids"]
}
```

Accept repeated multipart `images` in addition to the text/card fields. Require at least one of text, card, uploaded image, or existing image media id. Compare the Bearer token with `AUTOMATION_INTERNAL_API_TOKEN` using `hmac.compare_digest`.

**Step 4: Re-run focused validation tests**

Run: `.venv/bin/python -m pytest tests/test_group_ops_token_broadcast_api.py -q`

Expected: auth and validation tests pass.

### Task 2: Implement the one-shot broadcast command

**Files:**
- Create: `aicrm_next/automation/automation_engine/group_ops/broadcast.py`
- Modify: `aicrm_next/automation/automation_engine/group_ops/application.py`
- Test: `tests/test_group_ops_token_broadcast_api.py`

**Step 1: Write failing behavior tests**

Cover text-only, uploaded image, lesson card, combined content, duplicate idempotency, upload failure, queue failure, and dispatch failure. Use injected fake download/upload/dispatch dependencies; never call real WeCom or the lesson-card host.

**Step 2: Implement the command**

The command must:

1. Resolve the configured group-ops webhook plan and current bound groups.
2. Return the existing result before uploading when the idempotency key already exists.
3. For `card_path`, parse only the canonical article path and fetch only `https://ip.lhbl.com.cn/api/share/lesson-card/{lesson_id}.png`.
4. Validate image signatures and upload images through the existing Cloud Orchestrator command.
5. Build the existing normalized group message content and create one `external_effect_job`.
6. Dispatch only that exact job and return redacted receipt fields.

**Step 3: Re-run behavior tests**

Run: `.venv/bin/python -m pytest tests/test_group_ops_token_broadcast_api.py tests/test_group_ops_webhook_api.py tests/test_group_ops_external_effect_queue.py -q`

Expected: all tests pass and no real network call occurs.

### Task 3: Register the route and document the contract

**Files:**
- Modify: `aicrm_next/automation/automation_engine/group_ops/api.py`
- Modify: `docs/architecture/route_ownership_manifest.yml`
- Create: `docs/group_ops_broadcast_api.md`
- Test: `tests/test_next_api_docs_page.py`
- Test: `tests/test_route_ownership_manifest.py`

**Step 1: Add the route**

Register `POST /api/automation/group-ops/broadcast`. Keep the route outside `/api/admin`, require Bearer auth inside the handler, and return `X-AICRM-Route-Owner: ai_crm_next` plus the real external-call signal.

**Step 2: Add API documentation**

Document JSON and multipart examples, limits, idempotency, response fields, and explicit exclusions: no arbitrary remote image URLs, no admin-session fallback, and no tokens in responses.

**Step 3: Update route ownership**

Add the Next-native route to the ownership manifest with PostgreSQL production data and External Effect Queue dependencies.

### Task 4: Verify architecture and security

**Files:**
- Test: `tests/test_group_ops_token_broadcast_api.py`

**Step 1: Run focused and compatibility tests**

Run: `.venv/bin/python -m pytest tests/test_group_ops_token_broadcast_api.py tests/test_group_ops_webhook_api.py tests/test_group_ops_external_effect_queue.py tests/test_route_ownership_manifest.py -q`

**Step 2: Run static checks**

Run: `.venv/bin/python -m ruff check aicrm_next/automation/automation_engine/group_ops tests/test_group_ops_token_broadcast_api.py`

Run: `bash scripts/ci/check_architecture_boundaries.sh`

Run: `git diff --check`

**Step 3: Security review**

Confirm least-privilege token scope, constant-time comparison, strict path parsing, fixed-host cover fetch, image magic-byte checks, bounded payloads, idempotency before uploads, redacted responses, and zero real third-party calls in tests.
