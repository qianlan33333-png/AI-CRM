# 企业微信 Callback 生产 Cutover 调查报告（2026-06-28）

## 结论

2026-06-28 10:45 CST 已将生产企业微信回调从 nginx 临时 quick ACK 切到 AI-CRM Next 的独立 callback ingress（127.0.0.1:5002）。当前公网检查通过：侧边栏、后台页、Webhook Inbox、invalid callback app-level 400、quick ACK 关闭均已确认。

2026-06-28 11:00 CST 追加冷启动复核时发现生产 overlay 包漏落了部分源码文件；补齐 `webhook_inbox`、callback worker/ingress 源码和模板后，重启主 Web 复核通过。

2026-06-28 11:18 CST 已补齐生产压测、三类 worker isolation canary、rollback/re-cutover drill，并用最终 readiness 与 objective coverage 复核通过。

2026-06-28 11:24 CST 又做了一次重启固化复核：发现本地较新 `router_registry.py` 不能整文件覆盖旧生产 release，已恢复生产 release 原始文件后只插入 Webhook Inbox 注册点，并用 systemd drop-in 在 5001、5002、callback worker 启动前自动展开生产兼容 hotfix 包。最终复核通过。当前结论是：页面已恢复，nginx quick ACK 已关闭，生产企业微信 callback 永久修复目标已完成。

## 事故原因

1. 2026-06-27 的生产故障来自企业微信 callback 重试风暴打到主 Web 进程（5001），导致侧边栏和后台页面加载不稳定。
2. 当时的应急恢复方式是在 nginx 对 `/wecom/external-contact/callback` 和 `/api/wecom/events` 的 POST 直接返回 `success`，保护了主页面，但业务回调被绕过。
3. 生产 release 仍是旧代码，缺少 `webhook_inbox`、5002 ingress、worker、cutover 检查脚本与 migration，所以不能直接切 nginx，必须先补运行资产。

## 已执行操作

1. 备份生产文件：`/tmp/aicrm-release-wecom-cutover-file-backup-20260628T103455.tgz`。
2. 备份 nginx 配置：
   - `/tmp/nginx.conf.before-wecom-cutover-20260628T103455`
   - `/tmp/youcangogogo.conf.before-wecom-cutover-20260628T103455`
3. 部署最小 callback 永久修复包：
   - `webhook_inbox` repository/service/admin API/admin page
   - callback ingress / inbox worker
   - `0053_automation_agent_runtime_config` 前置 migration
   - `0054_webhook_inbox` migration
   - systemd units and ops check scripts
4. 执行 Alembic：当前 `alembic_version` 包含 `0054_webhook_inbox` 与原有 `0056_ai_audience_group_chat_members_view`。
5. 启动服务：
   - `openclaw-wecom-postgres.service` active
   - `openclaw-wecom-callback-ingress.service` active
   - `openclaw-wecom-callback-inbox-worker.timer` active
6. nginx cutover：
   - `nginx.conf` 增加 callback backpressure zone
   - `/etc/nginx/sites-enabled/youcangogogo.conf` 两个 callback location 改为 proxy `127.0.0.1:5002`
   - 移除 quick ACK `return 200 "success"`
7. 冷启动修复：
   - 补齐 `aicrm_next/platform/platform_foundation/webhook_inbox/*`
   - 补齐 `aicrm_next/platform/platform_foundation/webhook_inbox/templates/admin_console/webhook_inbox.html`
   - 补齐 `aicrm_next/channels/channel_entry/inbox.py`、`callback_ingress.py`、`callback_worker.py`、`callback_processor.py`、`ingress_app.py`
   - 重补 `router_registry.py` 与后台导航的 `webhook_inbox` 注册点
   - 重启 `openclaw-wecom-postgres.service` 后 `/health`、`/sidebar/bind-mobile`、`/admin/webhook-inbox` 通过
8. 最终验收补充：
   - 补齐当前 release 的 `scripts/run_wecom_callback_ingress.py`、`scripts/run_wecom_callback_inbox_worker.py` 与相关 ops 脚本，恢复 5002 冷启动能力
   - 执行 callback worker、internal event worker、external effect worker 三类停止隔离 canary
   - 重跑 1200/min 压测并通过 p95/p99 目标
   - 执行生产 rollback/re-cutover drill：短暂恢复 quick ACK，确认页面可用，再重新切回 5002
9. 重启固化修复：
   - `/home/ubuntu/aicrm-hotfix/wecom-callback-runtime-src-20260628.tar` 保存生产兼容 hotfix 包
   - 为 `openclaw-wecom-postgres.service`、`openclaw-wecom-callback-ingress.service`、`openclaw-wecom-callback-inbox-worker.service` 增加 `10-aicrm-callback-hotfix-runtime.conf` drop-in
   - 每次 service 启动前自动展开 hotfix 包到当前 release，降低正式 release 前重启丢 overlay 的风险

## 验证证据

1. Deploy smoke：`/tmp/wecom-callback-deploy-smoke.json`，结果 `ok=true`。
2. 公网最终状态：`/tmp/wecom-callback-public-state-final-fixed.json`，结果 `ok=true`、`permanent_fix_public_signals_ready=true`。
3. Quick ACK 状态：`/tmp/wecom-callback-quick-ack-after-cutover.json`，结果 `emergency_quick_ack_enabled=false`、`business_processing_suppressed=false`。
4. 合法加密 canary：
   - 公网 callback 返回 `200`
   - 响应头 `x-aicrm-app: ai_crm_wecom_ingress`
   - 入队证据 `/tmp/wecom-callback-ingestion-evidence-20260628T104648.json`，状态 `received`
   - 处理证据 `/tmp/wecom-callback-processing-evidence-20260628T104648.json`，状态 `succeeded`，`external_effect_job_ids=[]`
5. 首次压测：`/tmp/wecom-callback-pressure-20260628T104648.json`
   - callback：1200/1200 为 HTTP 200，失败 0
   - observed rate：约 1200/min
   - 主站采样：health、侧边栏、后台、Webhook Inbox 全部 200
   - callback 延迟：p50 36.9ms，p95 257.5ms，p99 554.1ms
6. 冷启动后复核：
   - Deploy smoke：`/tmp/wecom-callback-deploy-smoke-after-source-fix.json`，结果 `ok=true`
   - 公网状态：`/tmp/wecom-callback-public-state-after-source-fix.json`，结果 `ok=true`
   - Readiness：`/tmp/wecom-callback-readiness-after-source-fix.json`，`ready_for_production_cutover=true`、`ready_for_production_completion=false`
7. 目标覆盖检查：`temporary evidence output (objective-coverage-after-source-fix.json)`
   - `local_contract_ready=true`
   - `production_completion_ready=false`
   - 结论：当前满足“生产 cutover 可运行与页面恢复”，不满足目标文件定义的“永久修复最终完成”
8. 最终压测：`/tmp/wecom-callback-pressure-rerun-20260628T031508Z/wecom-callback-pressure.json`
   - callback：1200/1200 为 HTTP 200，失败 0
   - observed rate：1200.291/min
   - callback 延迟：p50 21.984ms，p95 36.605ms，p99 173.625ms
   - 页面采样：`/health` p95 4.339ms，`/sidebar/bind-mobile` p95 2.662ms，`/admin/automation-conversion` p95 7.945ms
9. 三类 isolation canary：
   - `/tmp/wecom-callback-isolation-20260628T031241Z/wecom-callback-worker-isolation.json`，`ok=true`
   - `/tmp/wecom-callback-isolation-20260628T031241Z/wecom-callback-internal-event-worker-isolation.json`，`ok=true`
   - `/tmp/wecom-callback-isolation-20260628T031241Z/wecom-callback-downstream-worker-isolation.json`，`ok=true`
10. Rollback/re-cutover drill：
   - `/tmp/wecom-callback-rollback-drill-20260628T031718Z/wecom-callback-rollback-evidence.json`，rollback checker `ok=true`
   - 已验证 emergency quick ACK 可恢复，随后 5002 cutover 可重新生效
11. 最终 readiness：
   - `/tmp/wecom-callback-final-completion-20260628T032420Z/wecom-callback-readiness-final.json`
   - `ok=true`、`ready_for_production_cutover=true`、`ready_for_production_completion=true`、`warnings=[]`
12. 最终目标覆盖：
   - `temporary evidence output (objective-coverage-final.json)`
   - `ok=true`、`local_contract_ready=true`、`production_completion_ready=true`
13. nginx `sites-enabled` 历史备份 include 风险清理：
   - 已将 5 个 `.bak-*` 普通文件从 `/etc/nginx/sites-enabled` 移动到 `/etc/nginx/backups/codex-sites-enabled-bak-risk-20260628T113730/`
   - 移动后 `find /etc/nginx/sites-enabled -maxdepth 1 -type f \( -name "*.bak*" -o -name "*.save" -o -name "*~" -o -name "*.old" \)` 返回为空
   - `sudo nginx -t` 通过，且不再输出重复 `server_name` warning
   - `sudo systemctl reload nginx` 成功
   - 复核：`nginx`、`openclaw-wecom-postgres.service`、`openclaw-wecom-callback-ingress.service`、`openclaw-wecom-callback-inbox-worker.timer` 均为 `active`
   - 复核：`127.0.0.1:5001/health`、`127.0.0.1:5002/health`、`127.0.0.1:5001/admin/webhook-inbox` 均通过；当前 callback 路径无效签名返回应用层 `400 invalid callback signature`

## 最终完成状态

目标文件中的完整验收项已经通过：

1. 通用 `webhook_inbox` schema、repository、幂等与重复折叠通过。
2. callback HTTP path 已收敛为 verify/decrypt/enqueue/ACK，DB 入队失败不假 ACK。
3. callback worker 可消费、重试、dead-letter、replay，并与 HTTP ACK 解耦。
4. internal event 与 external effect worker 停止时，不影响 callback ACK 或页面。
5. 1200/min 压测达到 p95<=200ms、p99<=500ms。
6. Webhook Inbox 管理后台、metrics、items、reconciliation API 可用。
7. rollback/re-cutover drill 已在生产执行并通过 checker。

## 剩余风险

1. 本次包含生产最小包 overlay，并已用 systemd drop-in 做重启前自动展开保护；但仍需要尽快把代码、migration、systemd、nginx 模板整理成正式 PR/release，避免长期依赖 hotfix 包。
2. 压测已达标，但建议继续把 callback ACK p95/p99 纳入持续监控，避免后续 DB 或 nginx 配置变化造成回退。

## 回滚方案

如出现 callback 新路径异常，可在生产执行：

```bash
sudo cp /tmp/youcangogogo.conf.before-wecom-cutover-20260628T103455 /etc/nginx/sites-enabled/youcangogogo.conf
sudo cp /tmp/nginx.conf.before-wecom-cutover-20260628T103455 /etc/nginx/nginx.conf
sudo nginx -t && sudo systemctl reload nginx
```

如需停止 5002 与 worker：

```bash
sudo systemctl disable --now openclaw-wecom-callback-ingress.service
sudo systemctl disable --now openclaw-wecom-callback-inbox-worker.timer
```

## 系统化治理建议

1. 发布治理：把 callback 永久修复纳入正式 PR、CI、release，不再依赖生产 overlay。
2. nginx 治理：把 `sites-enabled` 里的备份文件移到 `/etc/nginx/backups` 或 `/tmp`，避免 include 历史配置。
3. 运行治理：为 5001、5002、worker 分别建立 systemd health、journal alert、端口占用检查。
4. 回调治理：Webhook Inbox 做队列指标告警，包括 due、failed_retryable、dead_letter、oldest age。
5. 性能治理：对 callback ACK p95/p99 设置现实阈值并持续优化，目标是 1200/min 下 p95 稳定低于 250ms、p99 低于 500ms。
6. 演练治理：保留本次 rollback/re-cutover drill 证据，后续每次 release 后用同一 checker 做抽样复核。
