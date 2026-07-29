# AI-CRM Next Architecture Skill v1

后续所有 Codex 开发任务必须先引用并遵循本文件。本文件是 AI-CRM Next
架构开发 skill，不授权生产切换、未批准真实外呼、legacy 删除或 deploy/systemd/nginx
配置修改。

## A. 当前架构状态

- 默认 runtime 是 AI-CRM Next FastAPI modular monolith。
- `app.py run` 默认启动 `aicrm_next.main:app`。
- legacy Flask runtime / `production_compat` fallback 已退场；不得描述为当前
  production owner、rollback、hotfix 或 compatibility facade。
- `wecom_ability_service/` 已不在当前 live source tree；只允许作为历史参考名词
  出现在归档/closeout 证据中，不得重新引入或作为 fallback 依赖。
- `openclaw_service/` 和 `legacy_flask/openclaw_legacy/` 已物理删除，不得重新引入。
- MCP/OpenClaw 后续只允许通过 `aicrm_next.channels.integration_gateway` adapter boundary 承接。
- WeCom External Effect 真实执行已按 PR #1505 批准范围默认开启：仅限支持的
  WeCom effect_type、明确 target/sender/content 结构、审计、幂等和回滚口径。
  Payment / OAuth / OpenClaw / MCP / Webhook 以及其他真实外呼仍 blocked，不能
  未经审批打开。

## B. 开发分层

- API / HTTP / frontend_compat 层只解析请求、调用 application query/command、渲染响应。
- application 层负责编排 use case。
- domain 层只做本 context 的领域规则。
- read model 层只做只读投影，不承担写逻辑。
- integration_gateway 负责外部协议、legacy facade、adapter contract。
- infrastructure/shared 负责 runtime、配置、DB provider、审计、幂等等通用能力。

## C. 禁止事项

- 禁止在 frontend_compat 继续新增直接 SQL。
- 禁止 API 层直接 import 其他 context 的 `repo.py` 或 `service.py`。
- 禁止新增 `openclaw_service` import 或路径。
- 禁止把 fixture/local_contract/demo 数据伪装成 production 数据。
- 禁止未经 route ownership manifest 修改 production_compat catch-all。
- 禁止启用未经批准的真实外呼。WeCom External Effect 只能在 PR #1505 批准
  范围内执行；Payment / OAuth / OpenClaw / MCP / Webhook 仍需单独审批。
- 禁止修改 nginx/systemd/deploy production 配置，除非任务明确要求且有审批口径。
- 禁止把 checker 本地结果写成 production canary evidence。

## D. 每次 Codex 任务必须先回答

- 本任务属于哪个 capability owner？
- 涉及哪些 route？
- 这些 route 当前 owner 是 Next、legacy facade、还是 blocked？
- 是否涉及真实外部调用？
- 是否涉及生产数据？
- 是否有 fixture/local_contract 风险？
- 是否需要新增或更新 checker？
- rollback 是什么？

## E. 每个 PR 的输出格式

- Summary
- Architecture boundary
- Safety / non-goals
- Verification
- Risk / rollback
- Next action

## F. Legacy Growth Freeze

- 新功能默认使用 AI-CRM Next native implementation。
- 不要 Next 兼容层补丁；要完全基于 Next 架构做开发。若老版本能力需要兼容，以 `https://github.com/qianlan333/siyuan-crm` 为行为参考迁移重写到 Next 架构，而不是保留旧实现或新增兼容绕行。
- legacy facade / `production_compat` runtime 已移除，不允许作为 production compatibility / rollback / hotfix 边界恢复。
- 禁止恢复 `production_compat`。
- 禁止任何绕过 import guard 的动态 legacy import。

## G. Retired Legacy Runtime

- WeCom channel-entry callback owner is `aicrm_next.channels.channel_entry`.
- `/wecom/external-contact/callback`, `/api/wecom/events`,
  `/api/admin/channels/runtime-diagnosis`, and `/api/admin/channels/repair-entry`
  must not be forwarded to legacy Flask.
- Legacy channel-entry runtime is retired; rollback is a previous release
  rollback, not a dual-run fallback flag.
