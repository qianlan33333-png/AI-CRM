# Cloud Orchestrator Campaigns Route Inventory

Legacy Exit group 18 moved Cloud Orchestrator campaign read/workspace surfaces to Next exact routes and then locked the read rollback closed. This group does not execute campaigns, does not run run-due, does not send WeCom messages, and does not run the automation runtime.

## Frontend API Backend Contract Matrix

| 页面入口 | 前端模板/JS | 动作 | API | Method | Handler | Repo/Read Model | 外部副作用 | 本组决策 | Smoke |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/admin/cloud-orchestrator/campaigns` | `aicrm_next/frontend_compat/templates/admin_console/cloud_campaigns_workspace.html` | Campaign workspace page | page route | GET | `aicrm_next.extensions.growth.cloud_orchestrator.api.admin_cloud_campaigns` | Template over Next read API | none | Next page route owns the workspace; not a production_compat shell | page 200, contains campaign read URLs |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | list campaign groups and rows | `/api/admin/cloud-orchestrator/campaigns?limit=5000` | GET | `aicrm_next.extensions.growth.cloud_orchestrator.api.api_list_cloud_campaigns` | `ListCloudCampaignsQuery` / `CloudCampaignReadRepository.list_campaigns` | none | locked: Next exact read route; legacy rollback removed | API 200 or degraded empty, `route_owner=ai_crm_next` |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | open campaign drawer | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}` | GET | `aicrm_next.extensions.growth.cloud_orchestrator.api.api_get_cloud_campaign` | `GetCloudCampaignQuery` / `campaign_overview` | none | locked: Next exact read route for overview, segments, status counts, embedded steps | API 200 for fixture, controlled 404 if missing |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | view members drawer | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/members` | GET | `aicrm_next.extensions.growth.cloud_orchestrator.api.api_list_cloud_campaign_members` | `ListCloudCampaignMembersQuery` / `list_members` | none | locked: Next exact read route for member rows; legacy rollback removed | API 200 for fixture, controlled 404 if missing |
| `/admin/cloud-orchestrator/campaigns` | API-only validation | list flattened steps | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps` | GET | `aicrm_next.extensions.growth.cloud_orchestrator.api.api_list_cloud_campaign_steps` | `ListCloudCampaignStepsQuery` / `list_steps` | none | locked: Next exact read route; legacy rollback removed | API 200 for fixture, controlled 404 if missing |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | batch-start campaign group | `/api/admin/cloud-orchestrator/campaigns/batch-start` | POST | `api_batch_start_cloud_campaigns` | `BatchStartCloudCampaignsCommand` | SideEffectPlan only, `adapter_mode=real_blocked` | locked: Next CommandBus; legacy rollback removed | API 200, `source_status=next_command` |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | approve campaign | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/approve` | POST | `api_approve_cloud_campaign` | `ApproveCloudCampaignCommand` | none | locked: UI calls controlled write API | API 200, `source_status=next_command` |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | start campaign | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/start` | POST | `api_start_cloud_campaign` | `StartCloudCampaignCommand` | SideEffectPlan only, `adapter_mode=real_blocked` | locked: UI calls controlled write API | API 200, `source_status=next_command` |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | pause campaign | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/pause` | POST | `api_pause_cloud_campaign` | `PauseCloudCampaignCommand` | none | locked: UI calls controlled write API | API 200, `source_status=next_command` |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | reject campaign | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/reject` | POST | `api_reject_cloud_campaign` | `RejectCloudCampaignCommand` | none | locked: UI calls controlled write API | API 200, `source_status=next_command` |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | delete campaign | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}` | DELETE | `api_delete_cloud_campaign` | `DeleteCloudCampaignCommand` | none | locked: UI calls controlled write API | API 200, `source_status=next_command` |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` inline JS | create/edit/delete steps | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps*` | POST/PATCH/DELETE | `api_add_cloud_campaign_step` / `api_update_cloud_campaign_step` / `api_delete_cloud_campaign_step` | step mutation commands | none | locked: edit controls call controlled write API | API 200, `source_status=next_command` |
| timer / job runner | no page caller; API-only / timer-only | run due campaign delivery | `/api/admin/cloud-orchestrator/campaigns/run-due` | POST | `api_plan_cloud_campaign_run_due` | `PlanCloudCampaignRunDueCommand` | SideEffectPlan / AuditLedger / ExternalCallAttempt blocked record only | locked: Next safe-mode planner; no real send/runtime | API 200, `source_status=next_run_due_plan` |
| timer / preview | no page caller; API-only / timer-only | preview due campaign delivery | `/api/admin/cloud-orchestrator/campaigns/run-due/preview` | POST | `api_preview_cloud_campaign_run_due` | `PreviewCloudCampaignRunDueCommand` | due candidates / estimated actions only; no writes | locked: Next safe-mode preview; no real send/runtime | API 200, `source_status=next_run_due_preview` |

## Response Contract

Next campaign read JSON responses include:

- `ok`
- `items` / `campaigns` for list responses
- `campaign` for detail responses
- `members` / `rows` for member responses
- `steps` for step responses
- `count` and/or `total`
- `source_status=next_cloud_orchestrator_campaign_read`
- `route_owner=ai_crm_next`
- `fallback_used=false`
- `real_external_call_executed=false`
- `page_error` and `degraded=true` when production read storage is unavailable

## Side Effect Boundary

- No real WeCom send.
- No automation runtime.
- No campaign execute.
- No run-due execution.
- No real external storage.
- No payment/OpenClaw side effects.
- Media upload remains locked by the previous group and is not changed here.

## Decision Notes

Read/workspace GET routes are deletion_locked to Next exact read APIs with `legacy_fallback_allowed=false`. Campaign write, step mutation, and batch-start routes are locked on Next CommandBus with `legacy_fallback_allowed=false`, `delete_status=deletion_locked`, and `replacement_status=locked`. run-due and preview routes are separately locked on the Next safe-mode planner with production_compat rollback removed.

## Deletion Closeout Status Matrix

| 页面入口 | 前端模板/JS | 动作 | API | Method | Handler | Closeout 状态 | Smoke |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/admin/cloud-orchestrator/campaigns` | `cloud_campaigns_workspace.html` | Campaign workspace page | page route | GET | `aicrm_next.extensions.growth.cloud_orchestrator.api.admin_cloud_campaigns` | locked: Next shell over Next read APIs; write controls locked on Next CommandBus | page 200, non-empty |
| `/admin/cloud-orchestrator/campaigns` | inline JS | list campaigns | `/api/admin/cloud-orchestrator/campaigns` | GET | `api_list_cloud_campaigns` | locked: Next read model only, legacy fallback removed | API 200, `fallback_used=false` |
| `/admin/cloud-orchestrator/campaigns` | inline JS | detail drawer | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}` | GET | `api_get_cloud_campaign` | locked: Next read model only, legacy fallback removed | API 200 for fixture |
| `/admin/cloud-orchestrator/campaigns` | inline JS | members drawer | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/members` | GET | `api_list_cloud_campaign_members` | locked: Next read model only, legacy fallback removed | API 200 for fixture |
| `/admin/cloud-orchestrator/campaigns` | API-only validation | steps read | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps` | GET | `api_list_cloud_campaign_steps` | locked: Next read model only, legacy fallback removed | API 200 for fixture |
| `/admin/cloud-orchestrator/campaigns` | inline JS write controls | approve/start/pause/reject/delete/batch-start/step mutation | `/api/admin/cloud-orchestrator/campaigns*` | POST/PATCH/DELETE | Next CommandBus exact routes | locked: legacy rollback removed; deletion_locked | API 200, next_command |
| timer / job runner | no page caller; API-only / timer-only | run-due / preview | `/api/admin/cloud-orchestrator/campaigns/run-due*` | POST/OPTIONS | Next safe-mode planner | deletion_locked; production_compat rollback removed; no real send/runtime | preview/run-due/OPTIONS smoke |
