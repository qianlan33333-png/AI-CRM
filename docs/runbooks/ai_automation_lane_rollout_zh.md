# AI 自动化生成与群发 Lane 生产晋级

本 runbook 只用于 `ai_generation` 和 `wecom_ai_assistant_bulk`。两条 Lane
迁移后默认 `blocked`；不得用裸 SQL 修改 `queue_lane_policy`。

## 前置条件

- 生产 runtime 必须是 `active_generation=1`、`claim_enabled=true`、
  `rollout_mode=execute`、`external_claim_scope=all`。
- Provider 只读预检必须通过：AI 生成使用真实 production/staging gateway 且密钥、
  URL、模型齐全；企微必须启用真实私聊 effect。
- `blocked → canary` 必须列出当前 Lane 的全部 open job ID，并设置最大 open 数。
- 晋级过程不得重新创建人群事件、Agent item 或群发任务；保留原始幂等键续跑。

## 1. AI 生成单任务 canary

先 dry-run：

```bash
python scripts/ops/promote_ai_automation_lane.py \
  --lane ai_generation \
  --expected-generation 1 \
  --expected-policy-version queue-v2-production-all-g1 \
  --expected-mode blocked \
  --target-mode canary \
  --expected-capacity 4 \
  --target-capacity 4 \
  --expected-open-job-id '<reviewed-job-id>' \
  --max-open-jobs 1 \
  --actor '<operator>' \
  --reason '<reviewed-reason>'
```

应用时额外设置：

```bash
export AICRM_AI_AUTOMATION_LANE_ROLLOUT_AUTHORIZED=1
```

并传入：

```text
--apply \
--confirmation PROMOTE_AI_AUTOMATION_LANE_AI_GENERATION_BLOCKED_TO_CANARY_G1
```

验收必须确认生成 Effect 成功、provider result 被 continuation 消费、Agent item
写入 `generation_completed_at`，且只创建一个发送计划。

## 2. 企微单任务 canary

生成完成后取得新建的 `wecom_ai_assistant_bulk` Effect ID，再以同样方式执行
`blocked → canary`：

```text
--lane wecom_ai_assistant_bulk
--expected-open-job-id '<reviewed-wecom-effect-id>'
--confirmation PROMOTE_AI_AUTOMATION_LANE_WECOM_AI_ASSISTANT_BULK_BLOCKED_TO_CANARY_G1
```

验收口径是企微接口明确返回成功并有 `msgid`；不把“最终客户已经阅读”写成系统事实。

## 3. 晋级 execute

单任务 canary 全链通过、Lane 无 active dispatch、当前 open backlog 不超过审查上限后，
分别执行 `canary → execute`。初次保持容量 4，不在同一次变更里扩容。

## 回滚

任一阶段出现未知结果、Provider 错误、内容异常或重复风险，立即通过同一命令执行
`canary/execute → blocked`，容量保持不变。回滚只阻止新 claim；已经跨过 Provider
边界的任务不重放、不改写。`unknown_after_dispatch` 禁止自动重试。

每次应用都会：

- CAS 校验 generation、policy、scope、Lane mode 和容量；
- 保存 append-only `queue_lane_rollout_audit`；
- 保存非敏感 backlog ID/数量快照；
- 发送 PostgreSQL queue wakeup，但不在命令进程内调用 Provider。
