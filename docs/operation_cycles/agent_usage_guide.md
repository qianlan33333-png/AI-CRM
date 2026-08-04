# 运营闭环能力：Agent 使用手册

本文是所有 Agent 创建、更新或排查 CRM「运营闭环」时的入口文档。

一句话定义：**人可以在 CRM 发起当前动作，由单机本地连接器创建可对话 Codex 任务；CRM 仍只接收最终聚合结论和完整脱敏快照，真实发送继续由 AI 助手人工批准。**

## 1. 能力边界

运营闭环属于 AI-CRM Next 的 `operation_cycles` capability owner。

- 人在 CRM 发起动作；Codex 在本机负责取数、判断、文件生成和反复对话，人负责确认本地最终文件。
- CRM 只保存结构化快照、证据引用和当前投影，不扫描 Agent 本地目录。
- CRM 不保存本地 Excel、个人明细、本地路径、凭据或原始对话；动作请求只保存哈希、聚合结论和脱敏失败码。
- 运营闭环页面只创建本地 Codex 任务，不批准或直接发送。真实发送仍只允许从 AI 助手二级明细点击“确认并发送”。
- 失败后必须显式重新发起并关联 `parent_request_id`；不能自动重试或退回后台 `codex exec`。
- 所有报告必须声明 `external_effects="none"`；报告接口本身不会产生真实外部调用。
- 报告只能包含聚合数据和安全的脱敏说明，不能存逐人名单或原始附件。

动作发起和 Codex 任务创建的 `external_effects` 仍为 `none`。如果目标是绕过 AI 助手审批、自动发送或把逐人名单托管到 CRM，不应使用本能力。

## 2. 页面和接口

| 用途 | 路径 | 权限与说明 |
| --- | --- | --- |
| 任务列表 | `GET /admin/operation-cycles` | 管理员查看进度并发起当前动作 |
| 任务详情 | `GET /admin/operation-cycles/{strategy_key}` | 管理员只读；展示本周进度和三份 Markdown |
| 单次运行详情 | `GET /admin/operation-cycles/{strategy_key}/runs/{run_key}` | 管理员只读；展示完整执行证据 |
| 策略列表数据 | `GET /api/admin/operation-cycles/strategies` | 管理员会话，只读 |
| 策略详情数据 | `GET /api/admin/operation-cycles/strategies/{strategy_key}` | 管理员会话，只读 |
| 策略运行列表 | `GET /api/admin/operation-cycles/strategies/{strategy_key}/runs` | 管理员会话，只读 |
| 单次运行数据 | `GET /api/admin/operation-cycles/runs/{run_key}` | 管理员会话，只读 |
| Agent 上报 | `POST /api/operation-cycles/reports` | 仅 `ops_reporter` 机器身份可写 |
| 查询当前动作 | `GET /api/admin/operation-cycles/strategies/{strategy_key}/current-action` | 管理员会话；只返回一个动态动作 |
| 发起当前动作 | `POST /api/admin/operation-cycles/strategies/{strategy_key}/actions/{action_key}/start` | 管理员会话、CSRF、幂等；只排队本地任务 |
| 查询最终结论 | `GET /api/admin/operation-cycles/action-requests/{request_id}/result` | 管理员会话；不返回中间对话 |
| 执行器心跳 | `POST /api/operation-cycles/runner/heartbeat` | 仅 `operation_runner`；只上报逻辑绑定键 |
| 执行器领取 | `POST /api/operation-cycles/action-requests/claim` | 仅 `operation_runner`；出站长轮询 |
| 执行器事件 | `POST /api/operation-cycles/action-requests/{request_id}/events` | 仅 `operation_runner`；只允许 thread/turn 绑定、最终结果或失败码 |

`ops_reporter` 只能上报快照，`operation_runner` 只能心跳、领取和提交脱敏动作事件，`campaign_agent` 继续负责 Campaign preparation/context/提案。三类身份不能互换或扩大权限。

### 本地 Codex 动作工作流

1. 管理员在列表点击唯一的当前动作按钮。执行器离线、Codex 版本不兼容、缺本地绑定或已有未终结请求时，服务端必须阻断。
2. CRM 固化 `strategy_version`、`context_hash`、`skill_hash`、`run_key` 和幂等键，并将请求绑定到唯一在线 `runner_id`。
3. 本地连接器使用 `operation_runner` 长轮询领取，通过 Unix socket 调用固定兼容版本 Codex app-server 的 `thread/start` 与 `turn/start`。
4. 每个动作只创建一个持久 Codex task；连接器重启后复用已绑定的 `thread_id` / `turn_id`，不得创建第二个任务。
5. 人与 Codex 在本地反复修改。确认前不得创建 Campaign preparation、AI 助手计划或 `broadcast_jobs`。
6. `prepare_broadcast` 确认后由 Codex 使用独立 `campaign_agent` 调用现有 preparation/create 与 commit。最终 Excel SHA-256 必须同时作为 preparation `md_source_hash` 和动作结果哈希。
7. 服务端反查 preparation/plan；只有 `pending_review/draft` 且 `broadcast_jobs=0` 才接受最终结论。随后人到 AI 助手点击“确认并发送”。
8. 只有关联计划的系统发送事实全部进入终态后，`post_send_review` 才成为当前动作；正式 Skill 提案仍需管理员人工确认。

连接器配置、版本钉住、绑定文件和无发送验收见 [`local_codex_connector_runbook.md`](local_codex_connector_runbook.md)。

## 3. Agent 的标准工作流

每次执行按以下顺序处理：

1. 确定稳定的 `strategy_key` 和本次唯一的 `run_key`。
2. 完成当前阶段的真实工作，并只收集聚合事实。
3. 在本地构造一份完整 `operation_cycle_snapshot.v1`，不能只发送变化字段。
4. 运行模型校验和敏感数据扫描。
5. 为同一运行递增 `snapshot_revision`；新恢复执行必须新增 attempt，并设置 `parent_attempt_key`。
6. 使用新的 `report_id` 和 `Idempotency-Key` 提交快照。
7. 保存成功回执中的 `receipt_id`、`accepted_revision` 和 `snapshot_hash`。
8. 如果拥有管理员会话，再从只读接口核对最新投影；`ops_reporter` 本身不能读管理员接口。

```mermaid
flowchart LR
A[完成阶段工作] --> B[汇总脱敏事实]
B --> C[生成完整快照]
C --> D[本地校验]
D --> E[ops_reporter 上报]
E --> F[保存回执]
F --> G[管理员只读核对]
```

## 4. 认证方式

报告接口使用独立的 OAuth 2.0 client credentials 身份：

- client purpose：`ops_reporter`
- audience：`external_integration`
- scope：`write`
- capability：`operation_cycle_report_write`
- token endpoint：`POST /oauth/token`

Agent 必须使用运行环境注入的 client id 和 secret reference。不得把 client secret、access token 或 secret store 内容写进代码、日志、Markdown、快照、命令回显或交付文档。

本地连接器另用独立身份：

- client purpose：`operation_runner`
- audience：`external_integration`
- scope：`read write`
- capability：`operation_cycle_runner_heartbeat / operation_cycle_action_claim / operation_cycle_action_event_write`

`operation_runner` 不能读取客户、创建 Campaign、提交运营快照、审批计划或产生发送任务。

示意请求只保留占位符：

```bash
curl -X POST "${AICRM_BASE_URL}/api/operation-cycles/reports" \
  -H "Authorization: Bearer ${OPS_REPORTER_ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${IDEMPOTENCY_KEY}" \
  --data-binary @operation-cycle-snapshot.json
```

请求体最大为 512 KiB，`Idempotency-Key` 最大为 200 个字符。

## 5. 快照身份和版本规则

### `strategy_key`

代表长期重复运行的一项运营策略，例如 `hxc_monday_full_activation`。同一策略后续每周继续复用，不要把日期放进策略键。

### `run_key`

代表一次具体运行，例如 `hxc_monday_full_activation_20260720`。同一次运行的所有阶段快照必须使用同一个运行键。

### `report_id`

代表一份不可变报告，例如 `hxc_monday_20260720_postmortem_v4`。已经成功接受的 `report_id` 不能换内容重用。

### `snapshot_revision`

同一 `run_key` 从 1 开始单调递增。CRM 保存全部不可变快照，并将最高被接受 revision 作为最新投影。

### `Idempotency-Key`

- 同一个 key 和完全相同的 payload：返回原回执，不重复写入。
- 同一个 key 但 payload 不同：返回 `409`。
- revision 倒退或同 revision 内容冲突：返回 `409`。
- 网络超时后重试原请求时，必须继续使用原 key 和原 payload。

## 6. 顶层快照结构

报告模型是 `operation_cycle_snapshot.v1`，禁止额外字段。下面是结构模板；未观察到的数值必须使用明确空态，不能用 `0` 代替未知。

```json
{
  "schema_version": "operation_cycle_snapshot.v1",
  "external_effects": "none",
  "report_id": "strategy_20260720_postmortem_v1",
  "snapshot_revision": 1,
  "tenant_id": "aicrm",
  "reported_at": "2026-07-20T18:00:00+08:00",
  "strategy": {
    "strategy_key": "strategy_key",
    "title": "任务名称",
    "description": "任务说明",
    "cadence": "每周一",
    "timezone": "Asia/Shanghai",
    "status": "active",
    "version": 1,
    "version_label": "v1",
    "objective": "本策略长期目标",
    "definition": {}
  },
  "run": {
    "run_key": "strategy_20260720",
    "label": "2026-07-20 本周运行",
    "objective": "本次运行目标",
    "plan_version": "",
    "plan_status": "draft",
    "plan_source": "agent.aggregate",
    "started_at": "2026-07-20T09:00:00+08:00",
    "completed_at": null,
    "intended_send_at": "2026-07-20T16:00:00+08:00",
    "plan_scheduled_for": "2026-07-20T16:00:00+08:00",
    "first_sent_at": null,
    "last_sent_at": null
  },
  "execution_stage": "review",
  "review_status": "pending",
  "delivery_status": "not_started",
  "data_status": "collecting",
  "optimization_status": "none",
  "artifact_status": "partial",
  "attempts": [],
  "stages": [],
  "funnel": {},
  "metrics": [],
  "retrospective": {},
  "next_iteration": {},
  "references": [],
  "documents": {
    "broadcast_details": {"markdown": "", "generated_at": null},
    "retrospective_details": {"markdown": "", "generated_at": null},
    "execution_strategy": {"markdown": "", "generated_at": null}
  }
}
```

生产级示例可参考 [`../../fixtures/operation_cycles/hxc_monday_20260713_snapshot.json`](../../fixtures/operation_cycles/hxc_monday_20260713_snapshot.json)。复制示例时必须替换本次事实、键、时间、revision 和证据，不能把示例数字当成新一轮数据。

## 7. 六条状态轴

六条状态轴必须独立判断，不能用一个“已完成”覆盖所有状态。

| 状态轴 | 可用值 |
| --- | --- |
| `execution_stage` | `scheduled / preflight / decisioning / dry_run / review / delivery / observing / postmortem / closed` |
| `review_status` | `not_created / pending / approved / rejected / cancelled` |
| `delivery_status` | `not_started / waiting_window / dispatching / partial / completed / failed / cancelled` |
| `data_status` | `unavailable / collecting / early / mature / partial / attribution_gap` |
| `optimization_status` | `none / draft / pending_confirmation / accepted / rejected / applied` |
| `artifact_status` | `complete / partial / source_missing / snapshot_only` |

每个 `stage` 另有 `running / completed / blocked`。恢复执行时新增 attempt，不得覆盖被阻断的 attempt。

## 8. 漏斗和空值

固定漏斗字段：

1. `candidate_count`：候选人数
2. `audited_count`：完成审计人数
3. `recommended_send_count`：建议发送人数
4. `planned_target_count`：计划目标人数
5. `effective_sent_count`：有效发送人数
6. `failed_count`：失败人数

每个漏斗值都使用 `{status, value, data_source, limitation, classification}`。

| `status` | 含义 | `value` 规则 |
| --- | --- | --- |
| `observed` | 已由来源核验 | 必须有数值；可以为真实 `0` |
| `partial_lower_bound` | 只有可证明的下限 | 必须有数值并说明限制 |
| `not_started` | 尚未执行 | 必须为 `null` |
| `not_due` | 观察窗口未到 | 必须为 `null` |
| `unknown` | 当前无法判断 | 必须为 `null` |
| `not_applicable` | 本轮不适用 | 必须为 `null` |
| `blocked` | 因阻断无法获得 | 必须为 `null` |
| `instrumentation_missing` | 埋点缺失 | 必须为 `null` |

只有 `observed` 下的 `0` 才表示真实零。不要为了让页面“有数据”而把未知写成零。

## 9. 指标规则

每个 `metrics[]` 必须包含：

- `metric_key` 和 `label`
- `numerator`、`denominator`、`value`
- `unit` 和 `observation_window`
- `data_source` 和 `data_quality`
- 至少一条 `limitations`
- `value_status`
- 固定 `is_causal=false`

观察指标必须带分子和分母。窗口未到、来源缺失或埋点缺失时，三个数值都保持 `null`，并选择对应空态。

## 10. 三份 Markdown

页面左侧前三项完全由 `documents` 决定，CRM 不拆解其中业务字段。

| 文档键 | 页面名称 | 推荐结构 |
| --- | --- | --- |
| `broadcast_details` | 本周发送数据 | 发送时间、核心漏斗、失败分类、数据源、窗口和发送边界 |
| `retrospective_details` | 本周复盘明细 | 本周完成、结论、问题、限制、冲突、经验和待补证据 |
| `execution_strategy` | 下周执行策略 | 下周目标、执行顺序、前置条件、验证口径、待确认与不执行边界 |

三份文档均为可选默认空值；但进入 `postmortem` 后建议至少补齐发送数据和复盘明细，形成下一周策略时再补齐执行策略。

详细格式见 [`agent_markdown_contract.md`](agent_markdown_contract.md)。

### 安全图表

使用 `chart` 或 `echarts` fenced code block，JSON 支持：

- `type`：`bar / line / pie / funnel`
- `title`：最多 160 字
- `unit`：最多 24 字
- `labels`：1 至 40 项，每项最多 80 字
- `series`：1 至 8 组，每组数据长度必须与 labels 一致
- `pie` 和 `funnel` 只能有一组 series，且数值不能为负

```chart
{"type":"bar","title":"本周发送结果","unit":"人","labels":["有效发送","可重试失败"],"series":[{"name":"人数","data":[845,3]}]}
```

### 流程图

`mermaid` 仅支持基础 `flowchart` / `graph` 边关系和基础 `sequenceDiagram` 消息。复杂主题、脚本、点击事件、HTML 标签和任意插件不会执行。

## 11. 历史发送记录

第四项“历史发送记录”复用 AI 助手原数据，不是一份 Markdown。

Agent 必须在 `references` 中写入精确计划引用：

```json
{
  "reference_key": "ai-assistant-plan:strategy_20260720",
  "reference_type": "other",
  "label": "2026-07-20 AI 助手计划",
  "source_system": "cloud_orchestrator_plan",
  "source_id": "exact_plan_id",
  "href": "/admin/cloud-orchestrator/plans/exact_plan_id",
  "evidence_hash": "",
  "data_status": "unknown"
}
```

必须使用准确 `plan_id`，不得按标题、日期或人数模糊匹配。页面会读取 AI 助手原接口，点击“查看详情”进入原计划详情页。

## 12. 数据安全

报告会递归拒绝以下内容：

- 手机号、邮箱、unionid、external_userid、openid、昵称等个人标识
- 原始消息、逐人内容、逐人名单、收件人或用户 ID 数组
- access token、client secret、API key、私钥、密码和其他凭据
- `/Users/...`、`/home/...`、`file://...` 等本地文件路径
- 超出 schema 的任意字段

只保存来源名称、来源系统中的稳定聚合键、证据 SHA-256 和管理员可访问的安全链接。CRM 不托管原始 Excel、名单或 Agent 本地文件。

## 13. 成功回执与错误处理

成功响应示例：

```json
{
  "ok": true,
  "receipt_id": "ocrcpt_...",
  "strategy_key": "strategy_key",
  "run_key": "strategy_20260720",
  "accepted_revision": 1,
  "projection_updated": true,
  "snapshot_hash": "64-character-sha256"
}
```

常见错误：

| HTTP | 错误 | Agent 处理方式 |
| ---: | --- | --- |
| 400 | JSON 或 Idempotency-Key 不合法 | 修正本地请求；不要盲目换 key |
| 401 | 没有有效机器 token | 重新获取 `ops_reporter` token |
| 403 | purpose、scope 或 capability 不匹配 | 停止并修正机器身份，不能改用管理员会话绕过 |
| 409 | 幂等内容冲突、revision 冲突或倒退 | 读取本地历史和已保存回执，生成更高 revision 的完整快照 |
| 413 | 请求超过 512 KiB | 缩减 Markdown 或聚合内容，不能拆成零散增量 |
| 422 | schema、空值或敏感数据校验失败 | 根据 `validation_errors` 修正后再提交 |

任何失败响应都包含 `real_external_call_executed=false`。报告失败不代表真实发送被撤销或重试；发送系统与 CRM 映射是两个独立边界。

## 14. Agent 上报前检查清单

- [ ] capability owner 是 `operation_cycles`，未把逻辑塞进 `frontend_compat`。
- [ ] `strategy_key` 稳定，`run_key` 唯一且属于本次运行。
- [ ] 提交的是完整快照，不是零散字段补丁。
- [ ] revision 高于同一 run 已接受版本。
- [ ] 网络重试复用原 Idempotency-Key 和原 payload。
- [ ] 六条状态轴独立判断。
- [ ] 漏斗分母分开保存，未知没有写成零。
- [ ] 指标包含分子、分母、窗口、来源、质量、限制且 `is_causal=false`。
- [ ] 三份 Markdown 职责没有混写。
- [ ] AI 助手引用使用准确 `plan_id`。
- [ ] 快照不含个人标识、原始消息、逐人名单、凭据或本地路径。
- [ ] `external_effects` 固定为 `none`。
- [ ] 动作请求只保存聚合结论，没有本地路径、Excel 内容或原始对话。
- [ ] `operation_runner`、`campaign_agent`、`ops_reporter` 使用三个独立 purpose。
- [ ] `prepare_broadcast` 最终证据为 `pending_review/draft` 且 `broadcast_jobs=0`。
- [ ] `post_send_review` 只在系统发送事实全部终态后发起。
- [ ] 保存并交付 receipt，不把本地校验描述成生产已接受。

## 15. 本地验证

在 AI-CRM 仓库中可使用模型直接校验准备好的 JSON：

```bash
python - <<'PY'
import json
from pathlib import Path
from aicrm_next.extensions.hxc.operation_cycles.dto import OperationCycleSnapshotV1

payload = json.loads(Path("operation-cycle-snapshot.json").read_text(encoding="utf-8"))
validated = OperationCycleSnapshotV1.model_validate(payload)
print(validated.report_id, validated.snapshot_revision, validated.external_effects)
PY
```

这一步只证明本地 schema 和隐私校验通过，不是生产回执。生产是否接受只以报告接口返回的 receipt 为准。
