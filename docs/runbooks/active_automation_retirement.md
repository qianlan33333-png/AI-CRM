# Active Automation Retirement Guardrails

旧自动化运营 jobs runner 已退场：

- `/api/admin/automation-conversion/jobs/run-due` 必须返回 `404` 或 `410`。
- `/api/admin/automation-conversion/jobs/run-due/preview` 必须返回 `404` 或 `410`。
- `aicrm-automation-jobs-run-due.timer` 是 retired timer，不允许重新启用。

AI 自动化运营人群包由源事件推进 package dirty generation，并合并到每个 package
唯一的 durable refresh intent。`openclaw-ai-audience-scheduler.timer` 只在每天 02:00
写 daily intent；刷新执行归 PostgreSQL internal runtime，外推继续走独立
`external_effect_job`。

Cloud campaign run-due 不属于旧 automation jobs runner，保留 scheduled safe
mode 服务器验证 payload：

```json
{"operator":"aicrm-campaign-run-due","batch_size":200,"scheduled_safe_mode":true}
```
