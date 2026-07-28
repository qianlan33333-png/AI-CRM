# ADR: 单一持久化队列中的 AI 助手服务等级 Lane

- 状态：Accepted
- 日期：2026-07-28
- 所有者：Automation Agents / Cloud Orchestrator / External Effects

## 决策

AI 助手千级 1 对 1 私聊继续使用 `external_effect_job` 作为唯一外部调用队列，不引入 Redis、Celery 或旧定时器执行主链。新增逻辑 Lane `wecom_ai_assistant_bulk`，代码并发上限 24，数据库暗部署初始并发 4；普通 `wecom_bulk`、欢迎语和互动 Lane 保持原容量。

动态话术生成同样进入该持久化队列，Effect 类型为 `ai.agent.generate`，逻辑 Lane 为 `ai_generation`。代码并发上限 64，数据库初始并发 4 且暗部署保持 `blocked`。晋级只使用 4/8/16/32/64 五档，建议档位为 `ceil(P95 生成秒数 × 2 条/秒 × 1.25)` 向上取最近一档。

批次创建时冻结 Agent 的 published version、提示词、自动化类型、素材包、发送 webhook、人工审核标记和绑定人群包。收件人行与 `automation_agent.item.prepare` outbox 使用批量 SQL 在同一事务落库；固定话术由逐人准备消费者直接形成发送计划，动态话术只规划生成 Effect，内部事件消费者严禁调用模型。每个生成 Effect 成功后立即形成该收件人的发送计划，不等待整批完成。

审批后的 AI 单人发送计划在同一数据库事务中写入 `broadcast_jobs`、`external_effect_job`、`outbound_tasks` 和 Cloud Plan 投影。事务提交触发 PostgreSQL `NOTIFY`，目录调度器仅补偿未即时物化的合法漏单。稳定幂等键为 `broadcast-effect:<broadcast_job_id>:private:<external_userid>`。

每个 effect 保留独立内容、审计和供应商任务标识，不合并不同话术。`ordering_key` 按客户严格串行；`fairness_key` 按发送员工和批次轮转。

## 限流边界

`wecom_bulk` 与 `wecom_ai_assistant_bulk` 共享同一个 corp/app/operation `rate_scope_key`。单运行时进程使用 2 次/秒、burst=2 的 token bucket；429、45009、45011 会把当前速率减半，连续 5 分钟没有新限流后逐级恢复。

该启动限速器只协调单实例线程，不宣称跨实例一致。跨实例保护继续由 `queue_rate_scope_cooldown` 的持久化冷却负责。因此扩展到多外部 Worker 实例前，必须重新核算每实例启动速率或引入数据库协调，不能把 2 次/秒误当成集群总速率。

## Token 与连接

同一 corp/app 的客户群发客户端复用 `SingleFlightAccessTokenProvider`，一个 token 周期只允许一个刷新者；token 无效只刷新并重试一次。默认 HTTP transport 使用线程本地 `requests.Session`，复用该线程的 HTTPS 连接。刷新和限速计数写入 Worker heartbeat 的安全指标，不输出 token、corp id 或客户标识。

## 成功与失败语义

本阶段的“受理成功”是企微创建企业群发接口返回成功且提供可核对 `msgid`。客户最终收到不属于本阶段 SLA。

- 已知限流：进入持久化冷却并保留可重试状态。
- token 无效：仅允许一次刷新重试；再次失败按明确失败落库。
- 已开始供应商调用但结果未知：进入 `unknown_after_dispatch`，禁止自动重试。
- 审批事务内缺少身份、发送人或内容：不创建 Effect，保留 `broadcast_jobs=queued` 供目录调度器按原验证语义补偿或解释性阻塞。
- 重复审批、回调或进程恢复：依靠 Effect 幂等键、broadcast owner CAS 和外部队列租约，不创建第二个企微任务。
- 生成失败：可重试故障留在 Effect 租约与重试状态；耗尽、阻断、取消或过期通过 `external_effect.settled` 投影为逐人可解释失败。
- 生成成功后的进程崩溃：受限 provider result 由 durable completion consumer 恢复；稳定 callback event id 和发送 Effect 幂等键确保企微任务不重复。

## 发布与回滚

迁移默认把两条新 Lane 置为 `blocked`，不迁移旧队列记录。发布后 `ai_generation` 按 4→8→16→32→64、企微 Lane 按 4→8→16→24 提升数据库容量，并以授权测试对象完成 50→200→1000→真实千级晋升。

回滚先停止新流量路由，再按最后安全速率排空或暂停新 Lane。已经调用供应商的任务不重放、不改写；`unknown_after_dispatch` 不自动重试；旧队列记录不自动迁入或迁出新 Lane。
