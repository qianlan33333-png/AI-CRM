# PR-3 队列 Owner 原子切换（仅 dry-run）

本 runbook 只生成并审查切换计划，不授权生产执行、真实外呼或 generation 激活。生产切换必须另行批准，并使用固定 `pr3` owner inventory；禁止手工删减 timer/service 参数。

## 普通发布行为

- generation marker 不存在或为 `0`：部署事务临时停止并 drain 旧 timers 与 persistent callback worker；代码、迁移和 Web 健康完成后，只 restart 服务器已经安装的旧 unit，绝不从当前 checkout copy/enable 旧 unit。
- `aicrm-ai-audience-daily-intent.timer` 在切换前只预装，强制保持 disabled/inactive；旧 3 分钟 AI owner 继续通过 generation=0/claim gate closed 守卫运行。
- marker 大于 `0` 但 `AICRM_QUEUE_CUTOVER_COMMITTED=0`：旧 owner 必须 inactive/disabled，新 daily timer 也保持 disabled；部署不得自动恢复任何 owner。
- marker 大于 `0` 且 committed=1：后续部署只管理三个常驻 queue runtime 和已完成切换的 daily intent timer；旧 owner 仍须 inactive/disabled。

## Dry-run

```bash
python3 scripts/ops/cutover_queue_runtime_generation.py \
  --expected-generation 0 \
  --target-generation 1 \
  --expected-policy-version queue-v2-test-loopback \
  --lane internal_general \
  --lane internal_financial \
  --lane webhook_inbox \
  --lane wecom_interactive \
  --lane wecom_bulk \
  --lane wecom_media \
  --owner-inventory pr3 \
  --actor '<reviewed-actor>' \
  --reason '<reviewed-reason>'
```

预期 JSON 必须包含完整旧 owner 列表、`post_cutover_replacement_timers`、`claim_gate_change=not_applied` 和 `applied=false`。dry-run 不读写数据库、不操作 systemd、不触发 provider。

## 经批准切换的不变量

执行顺序固定为：确认 DB claim gate closed 且 `external_claim_scope=test_loopback`、policy snapshot 为 `queue-v2-test-loopback` → 写入 target marker（committed=0）→ 启动三个 canonical runtime 并等待完整 heartbeat → 停旧 timers → 停 persistent callback worker → drain 旧 services → disable 旧 units → 验证无双 owner且 daily replacement 仍 disabled → 在同一数据库事务中以 DB 当前时间为 cutoff 冻结四类旧 backlog并完成 generation CAS → 写 committed=1并启用 daily intent timer。

初次 generation 切换时 external runtime 只允许 `payload_json.execution_scope=test_loopback`；普通企微、支付、OAuth、MCP 和真实 outbound webhook 即使已经排队，也只能显示为 `policy_gated`，不得被 claim。进程 marker 中的 test-only 开关只是第二道 fail-closed 校验，不能替代数据库策略。

cutoff 前所有 unheld/open `external_effect_job`、`internal_event_consumer_run`、`internal_event_outbox`、`webhook_inbox` 必须写入 `queue_history_classification` 并设置 hold；provider boundary 不明的 External Effect 必须进入 quarantine/`unknown_after_dispatch`。cutoff 后 callback 行保留给新 generation。

任一 CAS 前步骤失败时 claim gate 保持 closed。CAS 后 daily replacement 启动失败时立即 disable claims、恢复 committed=0，并保持旧 owner retired；不得自动恢复 legacy runtime。

## 切换后只读复核

```bash
python3 scripts/ops/cutover_queue_runtime_generation.py \
  --expected-generation 0 \
  --target-generation 1 \
  --expected-policy-version queue-v2-test-loopback \
  --lane internal_general \
  --owner-inventory pr3 \
  --actor '<reviewed-actor>' \
  --reason '<reviewed-reason>' \
  --verify-owner-state
```

复核必须证明 DB generation/claim gate、三个 canonical runtime、旧 unit inactive/disabled 和 daily replacement active/enabled 一致。该命令不领取任务、不修改队列、不执行外呼。
