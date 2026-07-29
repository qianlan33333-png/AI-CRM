# Automation Agent Per-item Pipeline

AI 助手批次不再由一个 Worker 逐人串行调用模型。生产主链如下：

`webhook batch → automation_agent.item.prepare → ai.agent.generate（动态话术）/固定话术 → AI Audience send plan → broadcast_jobs + WeCom effect`

## Durable boundaries

- 批次和所有收件人先落 `automation_agent_webhook_batch/item`。
- 同一事务用一个批量 SQL 写入逐人 `automation_agent.item.prepare` outbox，并用稳定键 `automation_agent.item.prepare:<item_id>` 去重。
- 动态话术用 `automation_agent.generation:<item_id>:v<published_version>` 规划 `ai.agent.generate`；模型调用只允许发生在 External Effect adapter 中。
- Agent 配置在批次创建时冻结；长批次不会混用后续编辑的提示词、素材或审核策略。
- 生成成功的 durable completion 立即同步创建并审批单人发送计划；重复 completion、callback、租约恢复只复用既有业务行。

## Lanes and SLO

- `ai_generation`：代码上限 64，初始 4/blocked，晋级档位 4→8→16→32→64。
- `wecom_ai_assistant_bulk`：代码上限 24，初始 4/blocked，晋级档位 4→8→16→24。
- 生成容量建议：`ceil(P95 秒数 × 2 条/秒 × 1.25)` 向上取档。
- 企微启动速率：2 次/秒、burst=2。3000 条理论启动时间 25 分钟，预留 5 分钟给排队与抖动。

运行时只读快照按 Lane 暴露 backlog、最老等待、1 分钟吞吐、排队/调用 P95、预计排空、限流、token 刷新与受理率。其中 `workers[].metrics.wecom_api_auth_refresh` 只输出 provider 数量、刷新开始/成功/失败和缓存命中计数，不输出 token 或 corp/app 标识。批次行额外保存准备、生成、发送计划和失败原子计数，以及各阶段时间戳。

## Failure and rollback

- HTTP/网络/超时生成错误在 Effect 重试预算内恢复；配置错误和人工审核要求是解释性终态。
- provider result 仅保存于受限结果区；普通摘要只有供应商、模型、耗时、字符数和 usage。
- 模型 HTTP 调用前已经结束数据库 claim 事务，不占用连接池连接。
- 回滚先阻止新路由；已进入 Lane 的任务暂停或按最后安全速率排空。已跨供应商边界的任务不重放，旧队列记录不自动迁移。
