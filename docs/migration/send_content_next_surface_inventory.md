# Send Content Next Surface Inventory

## Rule

Any UI that lets an operator configure text copy, image materials, miniprogram materials, or attachment/PDF materials must use the standard Next-native send content surface:

- `AICRMSendContentComposer`
- `AICRMMaterialPicker`
- `SendContentPackage`

`SendContentPackage` is intentionally narrow and may only contain:

- `content_text`
- `image_library_ids`
- `miniprogram_library_ids`
- `attachment_library_ids`

Outer business pages own strategy, audience, sender, schedule, approval, and routing fields. Do not add fields such as `source_type`, `delivery_mode`, `audience_filter`, `sender_userid`, or `content_mode` to `SendContentPackage`.

## Next-Native APIs

- `POST /api/admin/send-content/validate`
- `POST /api/admin/send-content/preview`
- `GET /api/admin/material-picker/items`
- `GET /api/admin/automation-conversion/tasks/{task_id}`
- `PUT /api/admin/automation-conversion/tasks/{task_id}`
- `PUT /api/admin/automation-conversion/tasks/{task_id}/send-strategy`
- `PUT /api/admin/automation-conversion/tasks/{task_id}/send-content/unified`
- `PUT /api/admin/automation-conversion/tasks/{task_id}/send-content/profile-segments/{segment_key}`
- `PUT /api/admin/automation-conversion/tasks/{task_id}/send-content/behavior-segments/{segment_key}`
- `PUT /api/admin/automation-conversion/tasks/{task_id}/send-content/agent-materials`
- `GET /api/admin/automation-conversion/behavior-segment-rules`
- `POST /api/admin/hxc-dashboard/broadcast-tasks`

## Frontend Assets

- `aicrm_next/app/admin_console/static/admin_console/send_content_composer.js`
- `aicrm_next/app/admin_console/static/admin_console/send_content_composer.css`
- `aicrm_next/app/admin_console/static/admin_console/material_picker.js`
- `aicrm_next/app/admin_console/static/admin_console/material_picker.css`
- `historical removed reference (_automation_operation_orchestration_panel.html)`
- `historical removed reference (automation_operation_orchestration_panel.js)`
- `aicrm_next/app/admin_console/templates/admin_console/hxc_dashboard.html`
- `aicrm_next/automation/automation_engine/templates/admin_console/channel_code_form.html`
- `aicrm_next/automation/automation_engine/static/admin_console/channel_admission_pages.js`
- `aicrm_next/automation/automation_engine/group_ops/templates/admin_console/group_ops.html`
- `aicrm_next/automation/automation_engine/group_ops/static/admin_console/group_ops.js`
- `aicrm_next/app/admin_console/templates/admin_console/cloud_campaigns_workspace.html`

## Migrated

| Surface | 状态 | 外层负责 | 标准组件负责 | 备注 |
|---|---|---|---|---|
| 自动化运营编排 | retired | - | - | 旧 operation-task 编排面板已退场；新自动化触发与人群命中由 AI Audience 承接 |
| HXC 漏斗看板 | migrated | audience_filter / sender / idempotency_key | content_text + 三类素材 | 已接 Next-native `/api/admin/hxc-dashboard/broadcast-tasks`；真实外发仍由后续 dispatch 链路负责 |
| 渠道码中心欢迎语 | migrated | 渠道基础信息 / 入渠标签 | welcome_message + 三类素材 | adapter 映射到原 welcome 字段 |
| 群运营计划动作 | migrated | day / time / status / sort | 标准话术 + 三类素材 | legacy attachments 只兼容旧数据和发送 fallback |
| Campaign Step | migrated | day_offset / send_time / stop_on_reply | step 内容包 | 兼容旧 `content_text` / `content_payload_json` |

## Pending

| Surface | 状态 | 外层负责 | 标准组件负责 | 备注 |
|---|---|---|---|---|
| Sidebar 单发 | pending | 当前客户 / 发送模式 | 内容包 | 后续 PR 迁移；不得扩展 `SendContentPackage` |

## Legacy Only / Not Migrating

| Surface | 状态 | 外层负责 | 标准组件负责 | 备注 |
|---|---|---|---|---|
| 旧 Flask 模板 | legacy only | 旧生产兼容 | 不新增 | 禁止为标准组件迁移新增旧 Flask 实现 |
| 图片素材库管理页 | legacy only | 素材 CRUD / 缩略图 | 不适用 | 素材库自身允许调用素材库 API，不是业务发送内容配置入口 |
| 小程序素材库管理页 | legacy only | 小程序素材 CRUD / 缩略图 | 不适用 | 可继续使用图片选择器选择缩略图 |
| 附件素材库管理页 | legacy only | 附件素材 CRUD | 不适用 | 可继续调用附件库 API |

## Guardrails

- Migrated business pages must call `AICRMSendContentComposer.open`.
- Migrated business pages must not directly fetch `/api/admin/image-library`, `/api/admin/miniprogram-library`, or `/api/admin/attachment-library`.
- Business pages must not define private material pickers such as `attachMiniprogramPicker`, `setupWelcomeMaterialPicker`, or inline attachment JSON textareas.
- `AICRMMaterialPicker` is the only frontend code that may list selectable send-content materials for business pages.
- HXC broadcast must use `/api/admin/hxc-dashboard/broadcast-tasks` and must not call the old Flask `/api/admin/hxc-dashboard/broadcast` route.
- This migration work does not change real WeCom send, upload, media resolution, or outbound task execution.
