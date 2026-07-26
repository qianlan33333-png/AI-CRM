# Job Catalog Scheduler 单一 Owner 切换

## 目标与边界

本切换把以下三个生产 timer 的时钟责任收敛到
`aicrm-job-catalog-scheduler.timer`：

- `aicrm-next-broadcast-delegation.timer` → `campaign.plan`
- `aicrm-next-group-ops-planning.timer` → `group_ops.plan`
- `aicrm-ai-audience-daily-intent.timer` → `ai_audience.refresh`

统一 scheduler 同时执行 provider-free 的 `external_effect.reconcile`。
`payment.reconcile` 继续是 `observe_only`，其
`openclaw-wechat-pay-order-reconciliation-worker.timer` 不切换、不停用。
真实企微外发仍只能由 `external_worker` 执行。

## 发布前条件

1. Job Catalog observer 已在生产至少连续两个周期通过只读诊断。
2. 三个 handler 与 predecessor 使用同一 Next-native application 函数，且
   幂等、并发和 provider boundary 测试通过。
3. `internal_worker` 单一 owner 已独立发布并通过生产诊断。
4. 发布分支基于最新累计 `main`，完整 CI 与 merge SHA 的 main CI 均通过。

## 原子切换

生产提升必须只通过版本化 runtime manifest 执行：

1. deployment guard 阻止运行单元在迁移中自行启动。
2. 停止并 drain 当前 active timers/services。
3. disable/stop/reset 三个 predecessor timer/service；验证 inactive。
4. 删除六个 predecessor unit 文件并 daemon-reload。
5. 安装 scheduler owner service/timer。service 必须同时包含
   `AICRM_JOB_CATALOG_SCHEDULER_EXECUTE=1`、固定 confirmation 与 queue generation
   env；素材只写入耐久依赖，禁止 scheduler 同步上传企微素材。
6. 启动 scheduler timer，保持支付对账 timer 原样启用。
7. 验证部署状态后才解除 deployment guard。

禁止手工双开 predecessor 与 scheduler，也禁止为验证而直接执行真实企微发送。

## 完成证据

发布后运行 `job-catalog-scheduler-production-diagnostics.yml`，输入精确 release
SHA 与确认串 `DIAGNOSE AI-CRM JOB CATALOG SCHEDULER READ ONLY`。通过条件：

- 公网、服务器 checkout 与 `.release-sha` 三者一致。
- scheduler timer enabled/active，且 release 写入后至少成功退出一次。
- 六个 predecessor unit 均 disabled/inactive、LoadState=not-found，且 unit 文件不存在。
- 支付对账 timer 仍 enabled/active；catalog 中 payment 仍为 observe-only。
- catalog dry-run 为 `ok=true`、`executed_count=0`、无密钥和 provider 调用。
- scheduler unit 不含真实素材上传开关，企微素材上传仍由 `external_worker` 持有。

## 回滚

若部署或诊断失败，生产提升事务恢复上一精确 release SHA，并用上一版 manifest
重新安装三个 predecessor timer/service；候选 scheduler owner 配置随候选 release
一起撤销。回滚不删除队列表、不重放任务、不修改幂等键，也不允许同时保留新旧 owner。
