# AI-CRM

AI-CRM 是当前主线仓库，默认运行入口已经切到 AI-CRM Next FastAPI modular monolith。
Legacy Flask startup compatibility 已关闭；旧 Flask 代码仅作为非 startup maintenance/reference，等待后续单独归档。

当前主线包含：

- AI-CRM Next modular monolith
- CRM 后台控制台
- 问卷与公众号 OAuth 流程
- 客户中心 / 客户时间线（AI-CRM Next 默认 owner；旧 Flask D3 readonly owner 已退场）
- 自动化转化配置与阶段看板
- MCP / OpenClaw 集成（经 `aicrm_next.channels.integration_gateway` D7.7 adapter boundary）

## 当前主线口径

- GitHub `main` 是唯一源码主线。
- `python3 app.py run` 默认启动 `aicrm_next.main:app`。
- `app.py` 是 Next-only startup entrypoint；旧 Flask startup 命令已移除为硬错误。
- `openclaw_service/` 和 `legacy_flask/openclaw_legacy/` 已在 D9.6 后 physically removed，不是当前 live repo path。
- MCP / OpenClaw 后续只允许通过 `aicrm_next.channels.integration_gateway` 的 D7.7 adapter boundary 承接。
- 本地开发统一基于当前 Git clone，从最新 `main` 开功能分支。
- 仓库只保留源码、脚本、测试和文档，不再把发布包、导出物、临时备份和本机专属路径一起带进主仓。
- 生产 Nginx/systemd 切换仍需单独人工审批；本仓库入口切换不代表生产流量已经切换。
- WeCom External Effect 真实执行仅按 PR #1505 批准边界处理；Payment / OAuth / OpenClaw / MCP / Webhook 等其他真实外呼仍需单独审批。

## 建议先读

- [docs/skills/frontend-development-skill.md](docs/skills/frontend-development-skill.md)（任何前端 / 页面 / 组件 / UI / 管理后台任务开始前必须先读）
- [docs/development/ai_crm_next_architecture_skill.md](docs/development/ai_crm_next_architecture_skill.md)
- [docs/user_ops_v2.md](docs/user_ops_v2.md)
- [docs/deploy_runbook.md](docs/deploy_runbook.md)

如果任务偏 MCP / OpenClaw，再补：

- [docs/mcp_usage.md](docs/mcp_usage.md)

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py run
```

本地默认监听：

- `http://127.0.0.1:5001`，除非设置 `APP_PORT`

数据库 schema 变更：

```bash
python3 -m alembic upgrade head
```

Removed startup commands: `python3 app.py run-legacy`、`python3 app.py init-db`、`python3 app.py init-db-legacy`、`python3 app.py delete-questionnaire-submissions*`。这些命令仅作为 historical/deprecated note，不再是可执行入口。

## 测试基线

root 全量测试的标准环境是安装了 `requirements.txt` 的虚拟环境，root pytest 只收集顶层 `tests/`。系统 Python 如果没有安装 FastAPI、SQLAlchemy、Alembic 等依赖，直接运行 `python3 -m pytest -q` 会在 collection 阶段失败，这不是代码回归。

推荐入口：

```bash
scripts/run_tests.sh
```

等价手动命令：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

AI-CRM Next experiment workspace 已退休为 archive snapshot。历史实验
docs/tools/tests/fixtures/migrations 保留在
`docs/archive/experiments_ai_crm_next/`，不再作为独立测试入口。root
`aicrm_next/` 是唯一 live runtime source。

## 生产运维口径

主仓只保留生产发布、验收和审批边界，不记录具体 host、SSH alias、本地
identity、生产路径、白名单命令或 cookbook。需要生产诊断时，使用当前私有 ops
handoff 或由具备权限的人工提供一次性任务口径。

发布与验收口径见：

- [docs/deploy_runbook.md](docs/deploy_runbook.md)

## 仓库结构

- `app.py`
  - Next-only 应用入口，`run` 启动 AI-CRM Next，`health` / `routes` 输出 Next 诊断信息
- `aicrm_next/`
  - 当前默认 FastAPI modular monolith
- D9.6 已删除的历史路径
  - `wecom_ability_service/` 已不在当前 live source tree；旧 Flask/production
    compatibility fallback 不再是生产 owner 或 rollback/hotfix 边界
  - `openclaw_service/` 和 `legacy_flask/openclaw_legacy/` 不是当前 live source directory，不得重新引入
  - MCP / OpenClaw 当前架构入口是 `aicrm_next.channels.integration_gateway`
- `docs/`
  - 项目说明、运行口径、接口和方案文档
- `scripts/`
  - 备份、迁移、校验、回填脚本
- `tests/`
  - 回归测试与契约测试
- `deploy/`
  - 生产 service 配置参考

## 开发约定

- 开始开发前先同步最新 `main`。
- 任何新功能都从当前 clone 的最新 `main` 开分支。
- 业务改动优先补测试，再提 PR。
- 部署时只以已经合入 `main` 的提交为准。
- `dist/`、`exports/`、顶层归档包、临时页面和本机调试产物不要再进主仓。

## Worktree 工作流

如果你要并行推进多个分支项目，建议直接用仓库自带脚本管理 worktree，不依赖 Codex 当前的创建工作树 UI。

先同步主线：

```bash
git switch main
git pull --ff-only
```

创建一个新的分支 worktree：

```bash
scripts/worktree-new.sh feature/customer-timeline
```

默认会在当前仓库旁边创建一个目录，命名规则类似：

```text
<repo-parent>/aicrm-new-feature-customer-timeline
```

如果你要指定基线或目录，也可以这样：

```bash
scripts/worktree-new.sh feature/customer-timeline origin/main <custom-worktree-dir>
```

开发完成并合并后，移除 worktree：

```bash
scripts/worktree-remove.sh feature/customer-timeline
```

如果分支已经合并，也可以顺便删掉本地分支：

```bash
scripts/worktree-remove.sh feature/customer-timeline --delete-branch
```
