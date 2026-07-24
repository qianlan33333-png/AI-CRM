# Deploy Runbook

这份文档描述当前最常用的本地开发和生产发布口径。

当前代码默认运行入口已经切到 AI-CRM Next：

```bash
python3 app.py run
```

Legacy Flask startup compatibility 已关闭；不要再运行旧命令 `python3 app.py run-legacy` 或 `python3 legacy_flask_app.py run`。这些命令如出现在历史记录中，只代表迁移前口径，不是当前可执行入口。

这次入口变更本身不修改生产 Nginx/systemd，不代表生产流量已经切换。

## 本地仓库约定

- 当前 Git clone 就是本地唯一正式工作目录
- 新任务统一从最新 `main` 开分支
- 不再通过复制多个项目目录来并行开发

建议开始开发前先做：

```bash
git switch main
git pull --ff-only origin main
git switch -c <feature-branch>
```

## 生产环境

主仓只记录生产发布原则和审批边界，不记录具体外网入口、SSH alias、服务器路径、
环境变量文件、虚拟环境路径、白名单命令或可复制的运维 cookbook。需要生产诊断或
发布操作时，使用当前私有 ops handoff；任何写操作、DDL、systemd/nginx 切换或真实
外呼启用都需要单独人工审批。

当前代码入口已经是 AI-CRM Next；生产服务命令、Nginx upstream 和 callback runtime
切换是否完成，必须以私有 ops handoff 和当次只读验收结果为准，不能从仓库文档推断。

不要再默认假设：

- 存在长期运行的 `5000` 冷备实例
- 服务器上存在多份并行有效的发布目录

## WeCom Callback Runtime Isolation

目标架构把普通 Web 流量和企微 callback fast ACK ingress 分离，并用 worker 消费
`webhook_inbox`、internal event backlog 和 external effect jobs。仓库中的
`deploy/` 文件仅是版本化样例或待审批模板；不要把模板路径、unit 名称或本地 checker
输出写成生产已经切换的证据。

切换 callback ingress、worker、Nginx upstream 或两套同职责 unit/timer 的启停状态，
必须由人工按私有 ops handoff 执行并记录。Callback worker 的真实消费仍需先确认
dry-run / due 队列 / 下游隔离证据，不得在主仓文档中保留可直接复制执行的生产命令。

公开仓库只保留可安全讨论的 route 边界：

- `/wecom/external-contact/callback`
- `/api/wecom/events`
- `/api/admin/webhook-inbox/metrics`
- `/api/admin/webhook-inbox/items`
- `/api/admin/wecom/callback/reconciliation`

## 常用只读检查

只读检查可以确认健康页、关键页面和接口是否在线；具体 host、端口、服务名、日志命令、
白名单命令和查询 cookbook 走私有 ops handoff，不写入本仓库。

常用页面：

- `/admin`
- `/admin/customers`
- `/admin/questionnaires`
- `/admin/automation-conversion`
- `/api/admin/jobs/summary`

## 发布口径

当前生产发布仍是手工同步，但源码基线统一来自 GitHub `main`。
GitHub Actions 里 `main` push 只跑关键路径 smoke，避免每个小 PR 合并后
都等待全量 PG 回归；完整 `full-test` 保留在 nightly 和手动触发。

AI-CRM Next 已经成为默认代码入口，但生产 route/systemd/Nginx 切换仍必须走人工签核。

推荐顺序：

1. 在本地最新 `main` 上开发和测试
2. 提交分支并合并到 GitHub `main`
3. 服务器部署时只同步已经合入 `main` 的代码
4. 必要时按已审批计划安装依赖并执行 Alembic schema migration
5. 确认本次是否已获批切换 systemd/Nginx 命令
6. 做只读验收并记录证据来源

## 典型发布步骤

具体生产目录、env 文件、虚拟环境、systemd/nginx 操作和日志命令不写入主仓。生产
发布步骤以私有 ops handoff 为准；PR 中只能说明本次是否需要人工发布、schema
migration、systemd/nginx 切换或真实外呼审批。

不要在未经审批时修改生产 Nginx、systemd 或 route flag。不要把本地/staging evidence 写成 production canary 已执行。

## Runtime v2 真实发送验收队列隔离

真实发送验收必须先证明 due queue、timer/service 层级和下游隔离状态；不能只看单个
timer，也不能用本地 checker 代替生产证据。旧 automation runtime v2 due job 已退场；
生产验证应使用 AI Audience / external effect job 的当前链路，并且只在单独审批的
测试目标、sender 和内容边界内执行。

本仓库不保留可复制的 systemctl、SQL 或 worker 执行 cookbook。需要人工真实发送验收时，
由具备权限的 operator 按私有 ops handoff 执行，并在验收记录中说明原 timer 状态、
due queue guard、执行范围和恢复动作。

## 日志与备份

日志、备份目录和生产查询入口属于私有 ops handoff。PR、issue、agent entry 和公开文档
只记录证据类型、时间点、只读/写入边界和审批状态，不记录具体路径、命令或原始敏感
payload。

## 仓库与服务器清洁约定

- 服务器生产目录只保留一份正式代码
- smoke 目录、手工同步副本、历史发布包和 macOS 元数据不要长期堆在服务器
- 本地主仓不提交 `dist/`、`exports/`、顶层归档包、旧静态草稿页面

## 真实环境验收顺序

1. 先看服务和日志
2. 再看 `/health`
3. 再看关键页面和接口是否在线
4. 最后才做业务验收或写操作
