# Admin API

所有 `/api/admin/ai-audience/*` 接口必须 admin session 鉴权。响应不返回 SQL、secret、payload 明细或成员隐私字段。

## Package

- `GET /api/admin/ai-audience/packages`
- `POST /api/admin/ai-audience/packages`
- `GET /api/admin/ai-audience/packages/{package_id}`
- `PATCH /api/admin/ai-audience/packages/{package_id}`
- `POST /api/admin/ai-audience/packages/{package_id}/copy`
- `POST /api/admin/ai-audience/packages/{package_id}/pause`
- `POST /api/admin/ai-audience/packages/{package_id}/activate`
- `DELETE /api/admin/ai-audience/packages/{package_id}`

`POST create` 默认创建 draft/paused，不自动 active。`DELETE` 只 archive。

列表支持 `group_id={id}` 和 `group_id=ungrouped` 分组分页读取。复制人群包保留原分组；已绑定自动化话术的人群包必须先解除绑定才能归档。

## Package Groups

- `GET /api/admin/ai-audience/package-groups`
- `POST /api/admin/ai-audience/package-groups`
- `PATCH /api/admin/ai-audience/package-groups/{group_id}`
- `DELETE /api/admin/ai-audience/package-groups/{group_id}`

虚拟“未分组”只读。分组名大小写不敏感唯一；非空分组删除返回 `409 group_not_empty`。

## Version

- `POST /api/admin/ai-audience/packages/{package_id}/versions`
- `POST /api/admin/ai-audience/packages/{package_id}/preview`
- `POST /api/admin/ai-audience/packages/{package_id}/publish`

`publish` 可传 `version_id`；不传时发布 latest version。校验失败不能继续沿用旧 current version。

## Members

`GET /api/admin/ai-audience/packages/{package_id}/members`

只返回：

- `nickname`
- `external_userid`
- `entered_at`

## Automation Binding

- `GET /api/admin/ai-audience/packages/{package_id}/automation-binding`
- `PUT /api/admin/ai-audience/packages/{package_id}/automation-binding`
- `DELETE /api/admin/ai-audience/packages/{package_id}/automation-binding`

`PUT` 请求体为 `{ "automation_id": 123 }`。绑定以 `automation_agent_runtime_config.bound_package_key` 为真源，双方一对一；已停止或被其他包占用的能力不可新绑定。

## Retired Webhook Configuration

- `GET /api/admin/ai-audience/packages/{package_id}/webhooks`
- `PATCH /api/admin/ai-audience/packages/{package_id}/webhooks`

以上旧管理接口固定返回 `410 webhook_configuration_retired`；原 secret rotate 路由已移除。运营端不再读写任何接收或发送地址，第一方内部传递仅由自动化绑定用例维护。

## Senders

- `GET /api/admin/ai-audience/packages/{package_id}/senders`
- `PUT /api/admin/ai-audience/packages/{package_id}/senders`

发送人解析按 active whitelist 的 `priority ASC, id ASC` 取第一位；无命中时 `skip_reason=no_allowed_sender`。
