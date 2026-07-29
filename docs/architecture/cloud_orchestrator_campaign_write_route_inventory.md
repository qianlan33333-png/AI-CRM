# Cloud Orchestrator Campaign Write Controls Route Inventory

Legacy Exit group 19 moves Cloud Orchestrator campaign write controls to Next CommandBus and closes the production_compat rollback after validation. This group does not run run-due, does not execute campaigns, does not send WeCom messages, and does not run the automation runtime.

## Frontend API CommandBus Contract Matrix

| 页面入口 | 前端模板/JS | 动作 | API | Method | Handler | CommandBus | SideEffectPlan | UI 状态 | Smoke |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/admin/cloud-orchestrator/campaigns` | `aicrm_next/app/admin_console/templates/admin_console/cloud_campaigns_workspace.html` | workspace page | page route | GET | `aicrm_next.extensions.growth.cloud_orchestrator.api.admin_cloud_campaigns` | n/a | none | page over locked Next read APIs plus enabled write controls | page 200, non-empty |
| `/admin/cloud-orchestrator/campaigns` | inline JS `#cloud-camp-approve` | approve campaign | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/approve` | POST | `api_approve_cloud_campaign` | `ApproveCloudCampaignCommand` | none | enabled; 审批/受控写入 | API 200, `source_status=next_command` |
| `/admin/cloud-orchestrator/campaigns` | inline JS `#cloud-camp-reject` | reject campaign | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/reject` | POST | `api_reject_cloud_campaign` | `RejectCloudCampaignCommand` | none | enabled; 审批/受控写入 | API 200, audit recorded |
| `/admin/cloud-orchestrator/campaigns` | inline JS `#cloud-camp-start` | start campaign plan | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/start` | POST | `api_start_cloud_campaign` | `StartCloudCampaignCommand` | `effect_type=cloud_orchestrator.campaign.start`, `adapter_mode=real_blocked`, `requires_approval=true` | enabled; 仅生成启动计划 | API 200, SideEffectPlan only |
| `/admin/cloud-orchestrator/campaigns` | inline JS `#cloud-camp-pause` | pause campaign | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/pause` | POST | `api_pause_cloud_campaign` | `PauseCloudCampaignCommand` | none | enabled; 受控状态写入 | API 200, no runtime |
| `/admin/cloud-orchestrator/campaigns` | inline JS `#cloud-camp-delete` | delete campaign projection | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}` | DELETE | `api_delete_cloud_campaign` | `DeleteCloudCampaignCommand` | none | enabled; 本地/CommandBus 投影删除 | API 200, no storage delete |
| `/admin/cloud-orchestrator/campaigns` | inline JS group row | batch-start campaign group | `/api/admin/cloud-orchestrator/campaigns/batch-start` | POST | `api_batch_start_cloud_campaigns` | `BatchStartCloudCampaignsCommand` | `effect_type=cloud_orchestrator.campaign.start`, `adapter_mode=real_blocked`, `requires_approval=true` | enabled; 批量启动计划 | API 200, SideEffectPlan only |
| `/admin/cloud-orchestrator/campaigns` | inline JS step add | add step | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps` | POST | `api_add_cloud_campaign_step` | `AddCloudCampaignStepCommand` | none | enabled when campaign editable | API 200, audit recorded |
| `/admin/cloud-orchestrator/campaigns` | inline JS step editor | update step | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps/{step_index}` | POST/PATCH | `api_update_cloud_campaign_step` | `UpdateCloudCampaignStepCommand` | none | enabled when campaign editable | API 200, audit recorded |
| `/admin/cloud-orchestrator/campaigns` | inline JS step delete | delete step | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps/{step_index}` | DELETE | `api_delete_cloud_campaign_step` | `DeleteCloudCampaignStepCommand` | none | enabled when campaign editable | API 200, audit recorded |
| timer / job runner | no page caller; API-only / timer-only | run due campaign delivery | `/api/admin/cloud-orchestrator/campaigns/run-due` | POST | `api_plan_cloud_campaign_run_due` | `PlanCloudCampaignRunDueCommand` | SideEffectPlan / AuditLedger / ExternalCallAttempt blocked record only | separately locked by run-due group; no UI caller | API 200 |
| timer / preview | no page caller; API-only / timer-only | preview due campaign delivery | `/api/admin/cloud-orchestrator/campaigns/run-due/preview` | POST | `api_preview_cloud_campaign_run_due` | `PreviewCloudCampaignRunDueCommand` | due candidates / estimated actions only | separately locked by run-due group; no UI caller | API 200 |

## Deletion Closeout Status Matrix

| Surface | Routes | Owner after closeout | production_compat rollback | Registry / Manifest | External side effects |
| --- | --- | --- | --- | --- | --- |
| approve/reject/start/pause/delete | `/api/admin/cloud-orchestrator/campaigns/{campaign_code}/approve`, `/reject`, `/start`, `/pause`, DELETE `/api/admin/cloud-orchestrator/campaigns/{campaign_code}` | `next_command` | legacy fallback removed | `legacy_fallback_allowed=false`, `delete_status=deletion_locked`, `replacement_status=locked` | no real WeCom send; no automation runtime; start only creates `SideEffectPlan` |
| batch-start | `/api/admin/cloud-orchestrator/campaigns/batch-start` | `next_command` | legacy fallback removed | `legacy_fallback_allowed=false`, `deletion_locked`, `locked` | `adapter_mode=real_blocked`, `campaign_execute_executed=false`, `wecom_send_executed=false` |
| step mutation | POST `/steps`, PATCH/POST/DELETE `/steps/{step_index}` | `next_command` | legacy fallback removed | `legacy_fallback_allowed=false`, `deletion_locked`, `locked` | local projection/AuditLedger only; no HTTP client |
| read/workspace | GET `/admin/cloud-orchestrator/campaigns`, GET `/api/admin/cloud-orchestrator/campaigns*` | locked Next read/workspace | already removed | `deletion_locked`, `locked` | none |
| run-due / preview | `/api/admin/cloud-orchestrator/campaigns/run-due`, `/run-due/preview` | Next safe-mode planner | production_compat rollback removed | deletion_locked | preview/run-due/OPTIONS smoke |

## Command Contract

Each campaign write command carries:

- `command_id`
- `idempotency_key`
- `actor_id`
- `actor_type`
- `campaign_code` or `campaign_codes`
- `payload`
- `source_route`
- `dry_run`
- `trace_id`

All write responses include:

- `ok=true`
- `command_id`
- `source_status=next_command`
- `route_owner=ai_crm_next`
- `fallback_used=false`
- `real_external_call_executed=false`
- `campaign_execute_executed=false`
- `wecom_send_executed=false`
- `audit_event`
- `side_effect_plan` for start and batch-start

`Idempotency-Key` is accepted from the request header and returns the cached CommandBus result on replay.

## SideEffectPlan Boundary

Start and batch-start only create a SideEffectPlan:

- `effect_type=cloud_orchestrator.campaign.start`
- `adapter_mode=real_blocked`
- `requires_approval=true`
- `real_external_call_executed=false`
- `campaign_execute_executed=false`
- `wecom_send_executed=false`

Approve, reject, pause, delete, and step mutation update the local/fixture command projection in development and record AuditLedger evidence. They do not call WeCom, do not execute campaign runtime, and do not call direct HTTP clients.

## Out-of-Scope

- `/api/admin/cloud-orchestrator/campaigns/run-due`
- `/api/admin/cloud-orchestrator/campaigns/run-due/preview`
- real WeCom send
- real campaign execution
- automation runtime
- payment/storage/OpenClaw

## Registry Lifecycle

Campaign write controls are tracked as `runtime_owner=next_command`, `legacy_fallback_allowed=false`, `delete_status=deletion_locked`, and `replacement_status=locked`. The legacy fallback removed state is intentional after validation. Campaign read remains `deletion_locked`. run-due and preview are now separately deletion_locked on the Next safe-mode planner.
