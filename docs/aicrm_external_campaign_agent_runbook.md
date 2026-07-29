# AI-CRM 外部 Agent Campaign 审阅调用手册

> 适用对象：Codex / OpenClaw / Claude Code / 自定义 Agent。
> 当前状态：外部 Campaign 入口只允许进入“AI 助手待审阅计划”链路；禁止绕过审阅直接创建 `broadcast_jobs`。
> 安全提醒：不要把 client secret 或短期 JWT 写进本文档、GitHub、前端代码、日志、飞书公开文档或审计备注。

---

## 1. 当前硬规则

外部 Agent 发起私聊群发、定制化 Campaign、每周复盘推送等动作时，必须满足：

- 先生成 AI 助手待审阅计划，计划状态必须是 `review_status=pending_review`、`run_status=draft`。
- 审核前 `scheduled_jobs=0`，不允许出现可执行发送任务。
- 运营必须在 `/admin/cloud-orchestrator/plans` 或对应 AI 助手审阅页完成审核。
- 审核通过后，才允许由 cloud plan 审批链路生成 `source_type=cloud_plan` 的 `broadcast_jobs`。
- 外部 Campaign 入口不得直接创建 `source_type=external_campaign` 的 `broadcast_jobs`。
- 如果任务直接出现在任务列表/群发任务里，而没有先出现在 AI 助手审阅里，视为错误链路，必须停止并回滚/取消。

当前代码层也会保护这条规则：`POST /api/ai-assist/external/campaigns` 非 dry-run 请求会返回 `409 ai_assist_review_required`，不会写入 `broadcast_jobs`。

---

## 2. 可用接口

| 能力 | 方法 | 路径 | 当前用途 |
|---|---:|---|---|
| 外部 Campaign 预检 | POST | `/api/ai-assist/external/campaigns` | `campaign_agent` JWT；`dry_run=true` 时解析目标、校验话术/素材，返回 AI 助手审阅路径提示 |
| 外部 Campaign 真实创建保护 | POST | `/api/ai-assist/external/campaigns` | 不允许直排发送；返回 `409 ai_assist_review_required` |
| 查询历史 Campaign 状态 | GET | `/api/ai-assist/external/campaigns/{campaign_code}` | `campaign_agent` JWT；仅查询，不触发写入或发送 |
| AI 助手计划列表 | GET | `/api/admin/cloud-orchestrator/plans` | 运营后台查看待审阅计划 |
| AI 助手计划审核 | POST | `/api/admin/cloud-orchestrator/plans/{plan_id}/approve` | 人工审核后才会规划发送任务 |

所有接口挂在 `aicrm_next` FastAPI 应用下，响应里应带 `route_owner=ai_crm_next`。

---

## 3. 鉴权

Agent 必须使用独立注册的 `campaign_agent`，通过 TLS `POST /oauth/token` 以 `audience=external_integration`、`scope=read write` 换取短期 JWT。该 client 仅有 draft/status、受限客户读取和素材能力，不能审批、启动或直接发送。换取流程见 [`auth_client_credentials.md`](auth_client_credentials.md)。调用时只通过进程内环境变量注入短期 JWT，不要硬编码：

```bash
export AICRM_BASE_URL="https://www.youcangogogo.com"
export AICRM_ACCESS_TOKEN="<short-lived-client-credentials-jwt>"
```

所有请求都带：

```bash
-H "Authorization: Bearer ${AICRM_ACCESS_TOKEN}" \
-H "Content-Type: application/json"
```

不接受 `X-Internal-Api-Token`、query/path Token 或其他旧 fallback。

---

## 4. 支持输入

当前 dry-run 支持：

- `external_userid` / `external_contact_id`
- `unionid`
- `target_id + target_id_type`
- `external_userids`
- `recipients`
- 单人或多人定制话术
- 多 step 预检
- 图片、小程序、附件素材字段的基础结构校验

当前不支持直接输入：

- 手机号
- 手机号前 3 后 4 mask
- segment_code
- previous_campaign_failed / previous_campaign_unreplied
- A/B test 自动分流
- 每个用户多个 owner 自动择优

这些目标必须由 Agent 或上游系统先解析成明确的 `unionid` 或 `external_userid`，再进入预检或 AI 助手计划生成流程。

---

## 5. dry-run 预检

上线前或真实创建前，先加 `dry_run=true`：

```bash
curl -sS -X POST "${AICRM_BASE_URL}/api/ai-assist/external/campaigns" \
  -H "Authorization: Bearer ${AICRM_ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "dry_run": true,
    "owner_userid": "HuangYouCan",
    "external_userid": "wm_xxx",
    "scheduled_for": "2026-07-06 12:00",
    "timezone": "Asia/Shanghai",
    "message": "这是一条预检话术",
    "idempotency_key": "hxc_weekly_review_preview_20260706",
    "group_code": "hxc_weekly_review_20260706"
  }'
```

预期响应：

```json
{
  "ok": true,
  "dry_run": true,
  "side_effect_executed": false,
  "route_owner": "ai_crm_next",
  "source": "external_token_api",
  "send_path": "ai_assist_pending_review",
  "required_send_path": "ai_assist_pending_review",
  "forbidden_send_path": "direct_broadcast_job",
  "review_required": true,
  "review_status": "pending_review",
  "run_status": "draft",
  "scheduled_jobs": 0,
  "recipient_count": 1,
  "jobs": [],
  "previews": [
    {
      "status": "preview",
      "unionid": "union_xxx",
      "external_userid": "wm_xxx",
      "job_count": 1
    }
  ]
}
```

dry-run 的用途是确认“目标能解析、话术能生成、素材字段可被识别”。它不会创建 AI 助手计划，也不会创建发送任务。

---

## 6. 非 dry-run 保护响应

当前外部 Campaign 入口没有被允许直接真实创建。移除 `dry_run=true` 后，预期返回：

```json
{
  "ok": false,
  "error": "ai_assist_review_required",
  "phase": "ai_assist_review_guard",
  "route_owner": "ai_crm_next",
  "send_path": "ai_assist_pending_review",
  "required_send_path": "ai_assist_pending_review",
  "forbidden_send_path": "direct_broadcast_job",
  "review_required": true,
  "review_status": "pending_review",
  "run_status": "draft",
  "scheduled_jobs": 0
}
```

Agent 看到这个响应时，不能改用直写 `broadcast_jobs` 或直接调用任务接口。必须改走 AI 助手计划生成能力，并确认计划出现在审阅页。

---

## 7. 字段速查

| 字段 | 必填 | 说明 |
|---|---:|---|
| `owner_userid` / `sender` | 是 | 用哪个企微员工账号发送 |
| `external_userid` | 条件 | 单个目标用户；和 `external_contact_id` 等价 |
| `unionid` | 条件 | 单个目标用户 unionid |
| `external_userids` | 条件 | 多个目标用户，统一话术 |
| `recipients` | 条件 | 多个目标用户，每人可定制话术 |
| `scheduled_for` | 条件 | 顶层首发时间；没有时第一条 step 必须提供 |
| `message` / `content_text` | 条件 | 单步统一话术；有 `steps` 时可省略 |
| `steps` | 否 | 多步统一/定制话术 |
| `timezone` | 否 | 默认 `Asia/Shanghai` |
| `group_code` | 否 | 聚合标识；建议必传 |
| `group_label` | 否 | 人可读标题 |
| `intent` | 否 | 发送意图 |
| `idempotency_key` | 否 | 幂等键；建议必传 |
| `operator` | 否 | 默认 `external:{owner_userid}` |
| `dry_run` / `preview` | 否 | true 时只预检，不写 DB |
| `auto_backfill_automation_member` | 否 | 已退场；传 true 会返回 `410 automation_member_backfill_retired` |
| `use_campaign_workflow` | 否 | 已退场；传 true 会返回 `410 campaign_workflow_retired` |

至少需要 `external_userid`、`unionid`、`external_userids`、`recipients` 四者之一。

---

## 8. 错误处理

| HTTP 状态 | error | 含义 |
|---:|---|---|
| 401 | `access_token_required` | 没带 JWT |
| 401 | `invalid_access_token` / `access_token_expired` | JWT 无效或过期 |
| 403 | `invalid_target` / `scope_or_capability_required` | audience、scope、purpose 或 capability 越权 |
| 400 | `scheduled_for is required` | 没有可推导的首发时间 |
| 400 | `content_required` | 没有话术或附件 |
| 400 | `material_invalid` | 素材引用无法被发送 worker 消费 |
| 404 | `target_identity_not_found` | 身份表无法解析目标 |
| 409 | `ai_assist_review_required` | 外部 Campaign 不能直排发送，必须进入 AI 助手审阅 |
| 409 | `owner_mismatch` | 联系人 owner 与请求 owner 不一致且开启严格匹配 |
| 409 | `target_external_userid_missing` | 目标有 unionid 但缺企微 external_userid |
| 409 | `do_not_disturb` | 命中 active DND 且未显式 `bypass_dnd=true` |
| 410 | `automation_member_backfill_retired` | 旧 `automation_member` 回填参数已退场 |
| 410 | `campaign_workflow_retired` | 旧 segment/campaign/allocation workflow 已退场 |

Agent 规则：

- 401：停止并重新换取 Token；不得回退到共享 Token。
- 403：停止并检查 client purpose、audience、scope 与 capability，不得扩大 `campaign_agent` 权限绕过。
- 404：先修复身份解析，不要为了发送而回填旧 `automation_member` 或运营池快照。
- 409 `ai_assist_review_required`：这是保护性成功拦截，下一步必须创建 AI 助手待审阅计划。
- 409 DND：默认停止；只有用户确认后才传 `bypass_dnd=true`，并保留 warning。
- 410：移除退场参数，不要回退到旧 workflow。
- 任何 500：不要批量重试；先反馈错误并查服务日志。

---

## 9. Agent 标准流程

```text
1. 把用户输入解析成 target_id + target_id_type，优先使用 unionid 或 external_userid。
2. 确定 owner_userid。
3. 确定首发时间，必须是未来时间。
4. 生成稳定 idempotency_key 和 group_code。
5. 先 dry_run=true 调外部 Campaign 预检。
6. 如果返回 target_identity_not_found，先修复身份解析。
7. dry-run 通过后，创建 AI 助手 pending_review/draft 计划。
8. 确认 `/admin/cloud-orchestrator/plans` 中能看到该计划，且 target_count / pending_count 正确。
9. 等运营人工审核；审核前不得创建 broadcast_jobs。
10. 审核通过后，再验证生成的是 `source_type=cloud_plan` 的任务。
```

Agent 给用户的成功回复模板：

```text
已创建 AI 助手待审核运营计划。

- plan_id: xxx
- owner_userid: HuangYouCan
- target_count: N
- review_status: pending_review
- run_status: draft
- scheduled_jobs: 0

当前还不是“已发送”，需要在 AI 助手里审核通过后才会进入发送任务。
```

---

## 10. 严禁路径

以下做法都不允许：

```text
POST /api/ai-assist/external/campaigns 非 dry-run 后直接写 broadcast_jobs
直接 INSERT broadcast_jobs 作为外部 Campaign 创建结果
让任务直接出现在群发任务列表而没有 AI 助手待审阅记录
把 review_status=approved/run_status=active 当作外部创建默认值
用旧 automation_member/user_ops_pool_current 作为发送门禁或回填捷径
```

正确路径必须是：

```text
external data -> dry-run preview -> AI assistant pending_review plan -> human approve -> cloud_plan broadcast_jobs
```

---

## 11. 最小验收

### 11.1 本地测试

```bash
.venv/bin/python -m pytest -q tests/test_ai_assist_external_campaigns.py
.venv/bin/python -m py_compile aicrm_next/extensions/ai/ai_assist/external_campaigns.py aicrm_next/extensions/ai/ai_assist/api.py
```

### 11.2 生产 dry-run

```bash
curl -sS -X POST "${AICRM_BASE_URL}/api/ai-assist/external/campaigns" \
  -H "Authorization: Bearer ${AICRM_ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "dry_run": true,
    "owner_userid": "HuangYouCan",
    "external_userid": "wm_xxx",
    "scheduled_for": "2026-07-06 12:00",
    "message": "smoke test",
    "idempotency_key": "smoke_external_campaign_review_guard"
  }'
```

要求：

```text
ok=true
dry_run=true
side_effect_executed=false
send_path=ai_assist_pending_review
forbidden_send_path=direct_broadcast_job
jobs=[]
previews 至少 1 条
```

### 11.3 生产非 dry-run 保护

同样请求去掉 `dry_run=true` 后，要求：

```text
HTTP 409
error=ai_assist_review_required
phase=ai_assist_review_guard
scheduled_jobs=0
forbidden_send_path=direct_broadcast_job
```

同时查询：

```sql
SELECT COUNT(*) FROM broadcast_jobs WHERE source_type = 'external_campaign';
```

要求本次调用不会新增记录。
