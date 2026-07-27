# WeCom Tag Write Route Inventory

Status: Group 13 closeout deletion_locked

This inventory covers the WeCom Tag CRUD surfaces moved to Next CommandBus after the WeCom Tag Read legacy deletion was locked. Read routes stay `deletion_locked`; this closeout deletes the production_compat rollback for write routes and locks exact CRUD routes to Next CommandBus only. In production data mode, CRUD routes call the Next `WeComTagLiveGateway` for the real WeCom corp-tag write and then refresh the local projection through catalog sync. Local/fixture mode stays local-only.

## Frontend API Backend Contract Matrix

| Page entry | Frontend JS | Action | API | Method | Handler | Command | Registry | Manifest | Smoke |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/admin/wecom-tags` | `config_wecom_tags.html`; `wecom_tag_management.js` | 页面加载 | `/admin/wecom-tags`; read `/api/admin/wecom/tags`; read `/api/admin/wecom/tag-groups` | GET | `customer_tags.admin_pages`; `list_admin_wecom_tags_read_model`; `list_admin_wecom_tag_groups_read_model` | none | read routes `deletion_locked`; write closeout unaffected | read routes `next_exact` | page 200; not blank |
| `/admin/wecom-tags` | `wecom_tag_management.js` with generated `Idempotency-Key` | create tag | `/api/admin/wecom/tags` | POST | `create_admin_wecom_tag_command` | `CreateWeComTagCommand` | `next_command`, `legacy_fallback_allowed=false`, `deletion_locked` | `next_live_wecom_tag_write` in production data mode; local-only otherwise | 200, `source_status=next_command`, production `adapter_mode=live_wecom_tag_write` |
| `/admin/wecom-tags` | `wecom_tag_management.js` with generated `Idempotency-Key` | update tag | `/api/admin/wecom/tags/{tag_id}` | PUT or PATCH | `mutate_admin_wecom_tag_command` | `UpdateWeComTagCommand` | `next_command`, `legacy_fallback_allowed=false`, `deletion_locked` | `next_command`, `legacy_fallback_allowed=false`, `deletion_locked` | 200, `fallback_used=false` |
| `/admin/wecom-tags` | `wecom_tag_management.js` with generated `Idempotency-Key` | delete tag | `/api/admin/wecom/tags/{tag_id}` | DELETE | `mutate_admin_wecom_tag_command` | `DeleteWeComTagCommand` | `next_command`, `legacy_fallback_allowed=false`, `deletion_locked` | `next_live_wecom_tag_write` in production data mode; local-only otherwise | 200, production `real_external_call_executed=true` |
| `/admin/wecom-tags` | `wecom_tag_management.js` with generated `Idempotency-Key` | create group | `/api/admin/wecom/tag-groups` | POST | `create_admin_wecom_tag_group_command` | `CreateWeComTagGroupCommand` | `next_command`, `legacy_fallback_allowed=false`, `deletion_locked` | `next_live_wecom_tag_write` in production data mode; local-only otherwise | 200, production `adapter_mode=live_wecom_tag_write` |
| `/admin/wecom-tags` | `wecom_tag_management.js` with generated `Idempotency-Key` | update group | `/api/admin/wecom/tag-groups/{group_id}` | PUT or PATCH | `mutate_admin_wecom_tag_group_command` | `UpdateWeComTagGroupCommand` | `next_command`, `legacy_fallback_allowed=false`, `deletion_locked` | `next_command`, `legacy_fallback_allowed=false`, `deletion_locked` | 200, `fallback_used=false` |
| `/admin/wecom-tags` | `wecom_tag_management.js` with generated `Idempotency-Key` | delete group | `/api/admin/wecom/tag-groups/{group_id}` | DELETE | `mutate_admin_wecom_tag_group_command` | `DeleteWeComTagGroupCommand` | `next_command`, `legacy_fallback_allowed=false`, `deletion_locked` | `next_command`, `legacy_fallback_allowed=false`, `deletion_locked` | 200, no compatibility facade |
| `/admin/wecom-tags` | `wecom_tag_management.js` | sync | `/api/admin/wecom/tags/sync` | POST | `sync_admin_wecom_tags_command` | `execute_wecom_tag_catalog_sync` | `next_native_sync`, `legacy_fallback_allowed=false`, `deletion_locked` | `next_live_catalog_sync`, `legacy_fallback_allowed=false`, `deletion_locked` | 200, `sync_executed=true`, `source_status=next_live_remote_synced` |

No actual `/api/admin/wecom/tag-groups/sync` route exists in the current codebase, so it is not added as a Group 13 surface.

## Runtime Ownership

| Route | Methods | Registry runtime_owner | Manifest production_behavior | Rollback |
| --- | --- | --- | --- | --- |
| `/api/admin/wecom/tags` | POST, OPTIONS | `next_command` | `next_live_wecom_tag_write` in production data mode | production_compat rollback removed; `legacy_fallback_allowed=false` |
| `/api/admin/wecom/tags/{tag_id}` | PUT, PATCH, DELETE, OPTIONS | `next_command` | `next_live_wecom_tag_write` in production data mode | production_compat rollback removed; `legacy_fallback_allowed=false` |
| `/api/admin/wecom/tags/sync` | POST, OPTIONS | `next_native_sync` | `next_live_catalog_sync` | production_compat rollback removed; `legacy_fallback_allowed=false`; projection refresh only |
| `/api/admin/wecom/tags/sync-due` | POST, OPTIONS | `next_native_sync` | `next_live_catalog_sync` | production_compat rollback removed; `legacy_fallback_allowed=false`; projection refresh only |
| `/api/admin/wecom/tag-groups` | POST, OPTIONS | `next_command` | `next_live_wecom_tag_write` in production data mode | production_compat rollback removed; `legacy_fallback_allowed=false` |
| `/api/admin/wecom/tag-groups/{group_id}` | PUT, PATCH, DELETE, OPTIONS | `next_command` | `next_live_wecom_tag_write` in production data mode | production_compat rollback removed; `legacy_fallback_allowed=false` |
| `/api/admin/wecom/tags*` | POST, PUT, PATCH, DELETE, OPTIONS | `next_native` | `guarded_preview` | auxiliary out-of-scope Next subpaths only; no production_compat fallback |
| `/api/admin/wecom/tag-groups*` | POST, PUT, PATCH, DELETE, OPTIONS | `next_native` | `guarded_preview` | auxiliary out-of-scope Next subpaths only; no production_compat fallback |

Lifecycle for exact write routes is `delete_status=deletion_locked` and `replacement_status=locked`. The production_compat decorators for `/api/admin/wecom/tags`, `/api/admin/wecom/tags/{path:path}`, `/api/admin/wecom/tag-groups`, and `/api/admin/wecom/tag-groups/{path:path}` have been removed. The wildcard inventory rows now document auxiliary Next-owned out-of-scope surfaces, not legacy rollback.

## Backend Boundary

`aicrm_next/crm/customer_tags/api.py` exposes `write_router` and registers exact write routes before `production compatibility router` in `aicrm_next/main.py`.

`historical retired production_compat module` no longer registers WeCom tag read/write/sync exact or family fallback routes.

`aicrm_next/crm/customer_tags/commands.py` defines the write command shapes. `aicrm_next/crm/customer_tags/admin_write.py` owns CommandBus dispatch, validation, idempotency, audit recording, live write orchestration, catalog-sync orchestration, and response shape. `aicrm_next/crm/customer_tags/write_repo.py` owns `WeComTagWriteRepository` for local fixture mode and `PostgresWeComTagWriteRepository` for production tag catalog projection reads around update/delete responses.

Local/fixture successful command responses include `route_owner=ai_crm_next`, `source_status=next_command`, `fallback_used=false`, `real_external_call_executed=false`, `local_only=true`, and a `SideEffectPlan` exposed as `side_effect_plan` with `adapter_mode=real_blocked`. Production data mode successful command responses include `real_external_call_executed=true`, `sync_executed=true`, `local_only=false`, `write_model_status=live_wecom_synced`, and a `SideEffectPlan` exposed as `side_effect_plan` with `adapter_mode=live_wecom_tag_write`.

`aicrm_next/crm/customer_tags/sync_service.py` owns the sync route. It calls the Next `WeComTagLiveGateway`, normalizes remote `tag_group`/`tag` payloads, and refreshes only `wecom_corp_tag_groups` and `wecom_corp_tags` projection rows. A live success returns `route_owner=ai_crm_next`, `source_status=next_live_remote_synced`, `fallback_used=false`, `real_external_call_executed=true`, `sync_executed=true`, and `adapter_mode=live_catalog_sync`. Fixture/local contract mode returns `source_status=local_contract_refreshed`, `real_external_call_executed=false`, and `sync_executed=false`.

## Guardrails

Real WeCom create/update/delete is executed only in production data mode through `aicrm_next.integration_gateway.wecom_tag_live_gateway.WeComTagLiveGateway` using WeCom corp-tag APIs. After every successful live write, the catalog sync refreshes `wecom_corp_tag_groups` / `wecom_corp_tags`, so tag ids returned to users are WeCom tag ids that can be used by `mark_tag`. A production environment without PostgreSQL still returns `production_unavailable` instead of fixture writes.

Sync may execute the read-only WeCom tag catalog API and must not create/update/delete WeCom tags, tag groups, customer tags, questionnaire tags, payment records, storage assets, OpenClaw tasks, or automation runtime jobs. Sync writes are limited to the Next tag catalog projection tables and sync run evidence. CRUD routes are the only approved tag-catalog mutation routes in this inventory.

Frontend writes use `Idempotency-Key`; duplicate keys return the existing CommandBus result instead of creating duplicate audit/projection events.

Rollback is to revert the live CRUD orchestration to the previous local-projection command behavior while keeping production_compat removed. Do not restore production_compat decorators or legacy fallback.
