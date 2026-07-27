# P0-2 Internal Event Queue Final Verification

Baseline date: 2026-06-15

This document is the P0-2 closeout baseline for the Internal Event Queue before
P1 consumer auto-execute and External Effects real-execution gray rollout. It is
documentation only. It does not authorize production configuration changes,
worker execute expansion, real webhooks, WeCom, Feishu, payment query, refund,
or broadcast execution.

Final gate follow-up: see
[`internal-event-p0-2-final-gate.md`](internal-event-p0-2-final-gate.md) for the
full production verification, legacy-path observation markers, and repo-wide
coverage audit.

## 一、总体结论

P0-2 主体事件族已完成：

- `payment.succeeded`
- `questionnaire.submitted`
- `customer.tagged`
- `customer.untagged`
- `customer.phone_bound`
- `ai_campaign.created`
- `ai_campaign.approved`
- `ai_campaign.started`
- `ops_plan.approved`
- `broadcast_task.created`
- `owner_migration.executed`

当前基线是：

- 新增事件族持续开启 shadow emit，并通过 pair-aware worker allowlist 阻断自动执行。
- `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS` 应继续只包含
  `payment.succeeded:*` pairs。
- External Effects real execution 必须保持 disabled。
- 这不代表真实 webhook、企微、飞书、群发、支付查询或退款已经开放。
- 进入 P1 前，除非有单独审批和生产验收计划，否则必须继续保持
  payment-only pair allowlist。

`owner_migration.executed` 的 #1290 生产验收结论为 WARN 但可接受：主链路通过，
新 count semantics 字段已在生产正常样本验证；all-failed / partial-failure 未在生产强造，
由 #1290 自动化测试覆盖。

## 二、事件族完成矩阵

| event_type | feature flag | allowed_event_types 状态 | consumer fan-out | production verdict | PR / comment evidence | 当前是否持续开启 | 是否加入 allowed_event_consumers | 是否允许 worker auto execute | 是否真实外调 |
|---|---|---|---|---|---|---|---|---|---|
| `payment.succeeded` | `AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED` | included | 6 consumers: `order_projection_consumer`, `webhook_order_paid_consumer`, `automation_payment_consumer`, `customer_business_summary_consumer`, `dnd_policy_consumer`, `ai_assist_notify_consumer` | PASS with Stage 2 payment pairs; natural automation sample still pending | #1262, #1265, #1266, #1268 | yes | yes, 5 payment pairs only; webhook planner pair not currently allowed | yes, only configured payment pairs and only when worker auto-execute gate is on | no; External Effects disabled |
| `questionnaire.submitted` | `AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED` | included | 5 consumers: `questionnaire_projection_consumer`, `questionnaire_webhook_consumer`, `questionnaire_tag_consumer`, `automation_questionnaire_consumer`, `customer_summary_consumer` | PASS shadow emit + single-consumer gray; no worker auto-execute | #1269 | yes | no | no | no |
| `customer.tagged` | `AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED` | included | 3 consumers: `tag_external_effect_shadow_consumer`, `tag_summary_consumer`, `ai_assist_notify_consumer` | PASS shadow emit + pair allowlist blocking | #1270, #1271 | yes | no | no | no |
| `customer.untagged` | `AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED` | included | 3 consumers: `tag_external_effect_shadow_consumer`, `tag_summary_consumer`, `ai_assist_notify_consumer` | PASS shadow emit + pair allowlist blocking | #1270, #1271 | yes | no | no | no |
| `customer.phone_bound` | `AICRM_INTERNAL_EVENTS_CUSTOMER_IDENTITY_ENABLED` | included | 4 consumers: `customer_identity_projection_consumer`, `customer_summary_consumer`, `automation_phone_bound_consumer`, `customer_identity_ai_assist_notify_consumer` | PASS shadow emit + single-consumer projection gray | #1272, #1273 | yes | no | no | no |
| `ai_campaign.created` | `AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED` | included | 4 consumers: `campaign_summary_consumer`, `ai_campaign_ai_assist_notify_consumer`, `broadcast_task_planner_consumer`, `audit_projection_consumer` | PASS shadow emit + pair allowlist blocking | #1274 | yes | no | no | no |
| `ai_campaign.approved` | `AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED` | included | 4 consumers: `campaign_summary_consumer`, `ai_campaign_ai_assist_notify_consumer`, `broadcast_task_planner_consumer`, `audit_projection_consumer` | PASS shadow emit + pair allowlist blocking | #1274 | yes | no | no | no |
| `ai_campaign.started` | `AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED` | included | 4 consumers: `campaign_summary_consumer`, `ai_campaign_ai_assist_notify_consumer`, `broadcast_task_planner_consumer`, `audit_projection_consumer` | PASS shadow emit + pair allowlist blocking | #1274 | yes | no | no | no |
| `ops_plan.approved` | `AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED` | included | 4 consumers: `automation_schedule_refresh_consumer`, `ops_plan_ai_assist_notify_consumer`, `audit_projection_consumer`, `broadcast_task_planner_consumer` | PASS shadow emit + single-consumer gray; legacy alias compatibility covered | #1275, #1276 | yes | no | no | no |
| `broadcast_task.created` | `AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED` | included | 4 consumers: `broadcast_queue_projection_consumer`, `push_center_link_consumer`, `broadcast_task_ai_assist_notify_consumer`, `audit_projection_consumer` | PASS shadow emit, redaction, pair allowlist blocking; legacy alias compatibility covered | #1278, #1279, #1281, #1283, #1285, #1286 | yes | no | no | no |
| `owner_migration.executed` | `AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED` | included | 4 consumers: `customer_owner_projection_consumer`, `customer_summary_mark_dirty_consumer`, `owner_migration_ai_assist_notify_consumer`, `webhook_owner_migration_consumer` | WARN acceptable: normal production path PASS; all-failed / partial-failure covered by automated tests | #1288, #1290; #1290 verification comment `4707846493` | yes | no | no | no |

## 三、生产配置矩阵

### Internal Event Flags

Recommended P0-2 baseline:

```bash
AICRM_INTERNAL_EVENTS_ENABLED=1
AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED=1
AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED=1
AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED=1
AICRM_INTERNAL_EVENTS_CUSTOMER_IDENTITY_ENABLED=1
AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED=1
AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED=1
AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED=1
AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED=1
```

`AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES` should contain exactly the P0-2
event families currently approved for shadow emit:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES=payment.succeeded,questionnaire.submitted,customer.tagged,customer.untagged,customer.phone_bound,ai_campaign.created,ai_campaign.approved,ai_campaign.started,ops_plan.approved,broadcast_task.created,owner_migration.executed
```

`AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS` should remain payment-only:

```bash
AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS=payment.succeeded:order_projection_consumer,payment.succeeded:customer_business_summary_consumer,payment.succeeded:dnd_policy_consumer,payment.succeeded:ai_assist_notify_consumer,payment.succeeded:automation_payment_consumer
```

Do not add any of these pairs before a separate P1 approval:

```text
questionnaire.submitted:*
customer.tagged:*
customer.untagged:*
customer.phone_bound:*
ai_campaign.created:*
ai_campaign.approved:*
ai_campaign.started:*
ops_plan.approved:*
broadcast_task.created:*
owner_migration.executed:*
```

### External Effects

External Effects must remain disabled:

```bash
AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE=0
AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES=
```

The app settings layer should also keep every real execution gate false:

```bash
AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE=0
AICRM_EXTERNAL_EFFECT_PAYMENT_EXECUTE=0
AICRM_EXTERNAL_EFFECT_FEISHU_EXECUTE=0
AICRM_EXTERNAL_EFFECT_OPENCLAW_EXECUTE=0
AICRM_EXTERNAL_EFFECT_MEDIA_UPLOAD_EXECUTE=0
AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED=0
```

Expected diagnostics:

```json
{
  "real_execution_enabled": false,
  "execution_mode": "disabled",
  "allowed_effect_types": [],
  "real_external_call_executed": false
}
```

## 四、每个事件族摘要

### `payment.succeeded`

- 写路径：`aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py` 的 WeChat Pay notify /
  order paid closeout path。
- event schema：`event_type=payment.succeeded`,
  `aggregate_type=wechat_pay_order`, `aggregate_id=order.id || out_trade_no`,
  `subject_type=customer`, `idempotency_key=payment.succeeded:{out_trade_no}`,
  `source_module=public_product.h5_wechat_pay`。
- consumers：`order_projection_consumer`, `webhook_order_paid_consumer`,
  `automation_payment_consumer`, `customer_business_summary_consumer`,
  `dnd_policy_consumer`, `ai_assist_notify_consumer`。
- 生产验证结论：payment 主体完成，pair allowlist Stage 2 已配置到 payment-only
  pairs；External Effects disabled 下无真实 webhook。
- 当前遗留限制：`automation_payment_consumer` 仍需下一笔自然支付样本证明 worker
  自动执行；`webhook_order_paid_consumer` 不在当前 allowed pairs 中。

### `questionnaire.submitted`

- 写路径：`aicrm_next/extensions/forms/questionnaire/h5_write.py`，H5 submit 持久化成功后
  `safe_emit(emit_questionnaire_submitted_shadow_event, ...)`。
- event schema：`event_type=questionnaire.submitted`,
  `aggregate_type=questionnaire_submission`, `aggregate_id=submission_id`,
  `idempotency_key=questionnaire.submitted:{submission_id}`,
  `source_module=questionnaire.h5_write`。
- consumers：`questionnaire_projection_consumer`, `questionnaire_webhook_consumer`,
  `questionnaire_tag_consumer`, `automation_questionnaire_consumer`,
  `customer_summary_consumer`。
- 生产验证结论：shadow emit、consumer fan-out、redaction、single-consumer gray
  通过；worker auto-execute 被 payment-only pair allowlist 阻断。
- 当前遗留限制：真实 questionnaire webhook / tag / automation 仍未开放；
  `tests/test_external_effects_mvp.py` 的既有 questionnaire submit 400 需单独处理。

### `customer.tagged` / `customer.untagged`

- 写路径：`aicrm_next/crm/customer_tags/live_mutation.py`，tag mark/unmark
  side-effect plan 之后 `safe_emit(emit_customer_tag_shadow_event, ...)`。
- event schema：`event_type=customer.tagged|customer.untagged`,
  `aggregate_type=customer`, `subject_type=customer`,
  idempotency key 为 `customer.tagged:{command}` 或
  `customer.untagged:{command}`。
- consumers：`tag_external_effect_shadow_consumer`, `tag_summary_consumer`,
  `ai_assist_notify_consumer`。
- 生产验证结论：shadow emit 与 pair allowlist blocking 通过；消费者只复用或确认
  shadow side-effect plan，不调用 WeCom。
- 当前遗留限制：不允许将 shared `ai_assist_notify_consumer` 通过 consumer-name-only
  allowlist 间接放开；真实 WeCom tag mark/unmark 仍关闭。

### `customer.phone_bound`

- 写路径：`aicrm_next/crm/identity_contact/application.py`，
  `BindMobileToExternalContactCommand` 成功绑定后 emit。
- event schema：`event_type=customer.phone_bound`,
  `aggregate_type=customer`, `aggregate_id=person_id || external_userid || mobile_hash`,
  `subject_type=customer`,
  `idempotency_key=customer.phone_bound:{stable_identity_key}:{mobile_hash}`,
  `source_module=identity_contact.application`。
- consumers：`customer_identity_projection_consumer`, `customer_summary_consumer`,
  `automation_phone_bound_consumer`, `customer_identity_ai_assist_notify_consumer`。
- 生产验证结论：shadow emit、4 consumer fan-out、single-consumer projection gray
  通过；无外调。
- 当前遗留限制：summary、automation、AI notify 仍是未配置或 shadow-only；不得加入
  worker pair allowlist。

### `ai_campaign.created` / `ai_campaign.approved` / `ai_campaign.started`

- 写路径：`aicrm_next/extensions/growth/cloud_orchestrator/campaigns_write.py`，
  create / approve / start command 成功后 emit。
- event schema：`aggregate_type=ai_campaign`, `aggregate_id=campaign_code`,
  `subject_type=ai_campaign`, `subject_id=campaign_code`,
  `source_module=cloud_orchestrator.campaigns_write`；idempotency key 按 lifecycle
  fact 区分 created / approved / started。
- consumers：`campaign_summary_consumer`, `ai_campaign_ai_assist_notify_consumer`,
  `broadcast_task_planner_consumer`, `audit_projection_consumer`。
- 生产验证结论：三个 lifecycle event 均已 shadow emit 验证；consumer fan-out 与
  pair allowlist blocking 通过。
- 当前遗留限制：不创建真实 broadcast task，不发 WeCom/Feishu，不开放 planner
  auto-execute。

### `ops_plan.approved`

- 写路径：`aicrm_next/extensions/growth/cloud_orchestrator/application.py`，
  `ApproveCloudPlanCommand.execute` 审批成功后 emit。
- event schema：`event_type=ops_plan.approved`,
  `aggregate_type=cloud_orchestrator_plan`, `aggregate_id=plan_id`,
  `subject_type=ops_plan`, `subject_id=plan_id`,
  `idempotency_key=ops_plan.approved:{aggregate_type}:{plan_id}:{approved_marker}`,
  `source_module=cloud_orchestrator.application`。
- consumers：`automation_schedule_refresh_consumer`,
  `ops_plan_ai_assist_notify_consumer`, `audit_projection_consumer`,
  `broadcast_task_planner_consumer`。
- 生产验证结论：shadow emit、4 consumer fan-out、single-consumer gray、pair allowlist
  blocking 通过；legacy `ai_assist_notify_consumer` 为 dispatch-only alias。
- 当前遗留限制：automation schedule refresh 与 broadcast task planner 仍不可 worker
  自动执行；真实外调仍关闭。

### `broadcast_task.created`

- 写路径：
  `aicrm_next/extensions/growth/cloud_orchestrator/application.py`,
  `aicrm_next/automation_engine/group_ops/action_dispatcher.py`,
  `aicrm_next/integration_gateway/wecom_group_adapter.py`,
  AI Audience outbound planner / external_effect_job。
- event schema：`event_type=broadcast_task.created`,
  `aggregate_type=broadcast_task`, `aggregate_id=task_id_or_code`,
  `subject_type=broadcast_task`, `subject_id=task_id_or_code`,
  `idempotency_key=broadcast_task.created:{task_id_or_code}`。
- consumers：`broadcast_queue_projection_consumer`, `push_center_link_consumer`,
  `broadcast_task_ai_assist_notify_consumer`, `audit_projection_consumer`。
- 生产验证结论：shadow emit、redacted stored payload/list/detail、4 consumer fan-out、
  pair allowlist blocking 通过；legacy `ai_assist_notify_consumer` 为 dispatch-only
  alias。
- 当前遗留限制：不代表任务已审批、已群发、已私聊发送、已推送 webhook 或已执行
  External Effects；真实 group/private/broadcast execution 仍关闭。

### `owner_migration.executed`

- 写路径：`aicrm_next/crm/owner_migration/application.py`,
  `OwnerMigrationService.execute_scoped`, legacy `_run_legacy` execute path。
- event schema：`event_type=owner_migration.executed`,
  `aggregate_type=owner_migration`, `aggregate_id=migration_id_or_batch_id`,
  `subject_type=owner_migration`, `subject_id=aggregate_id`,
  `idempotency_key=owner_migration.executed:{aggregate_id}`,
  `source_module=owner_migration.application`。
- consumers：`customer_owner_projection_consumer`,
  `customer_summary_mark_dirty_consumer`,
  `owner_migration_ai_assist_notify_consumer`,
  `webhook_owner_migration_consumer`。
- 生产验证结论：#1288 主链路 PASS/WARN，#1290 count semantics 正常样本 PASS/WARN；
  新 event 生成、4 consumer_run pending、pair allowlist blocking、无 external effects。
- 当前遗留限制：生产未强造 all-failed / partial-failure；#1290 自动化测试覆盖这些
  count semantics。不得加入 `owner_migration.executed:*` worker pairs。

## 五、风险与遗留项

### 1. External Effects real execution 多次回潮

生产验证中多次发现 `real_execution_enabled` drift back to `true`。这说明
app_settings、env、diagnostics 和启动默认值之间仍有配置回潮风险。

P1 前必须完成：

- 启动校验：生产启动时若真实执行开关与 rollout stage 不匹配，应 fail closed 或
  输出强告警。
- diagnostics guard：Internal Event 验收和 worker execute 前强制检查
  `real_execution_enabled=false`、`allowed_effect_types=[]`。
- app_settings-env 优先级收敛：明确哪一层是最终事实源，避免 env 已关但 app_settings
  又把真实执行打开。

在这些 guard 完成前，不得打开 External Effects real execution。

### 2. payment automation 自然样本缺口

`payment.succeeded` 主体事件族完成，`automation_payment_consumer` Stage 2 pair
allowlist 已配置。仍需下一笔自然支付证明 worker 自动执行路径：

- 只处理 `payment.succeeded:automation_payment_consumer` 等已批准 payment pairs。
- 不触发 payment query、refund、webhook real execution。
- `external_effect_attempt` 不新增。

### 3. owner_migration partial/all-failed 生产样本缺口

#1290 已修复 `owner_migration.executed` count semantics，并用自动化测试覆盖：

- all-failed transfer
- partial failure
- explicit success count
- zero CRM update
- happy path
- redaction

生产只执行了 0-candidate safe sample，未强造真实失败迁移。WARN 可接受，不应为了
覆盖 all-failed / partial-failure 而影响真实客户归属。

### 4. run_due preview 需要短期 Worker JWT

部分历史生产复验因当时缺共享 Bearer 只能验证到 HTTP 401。RAUTH 后旧凭据已删除，当前必须使用注册 `automation_worker` 换取的短期 JWT，再结合 diagnostics 的 pair allowlist counters 证明阻断。

后续正式 P1 验收应准备：

```bash
AICRM_ACCESS_TOKEN=<short-lived automation_worker jwt>
```

并使用 preview / dry-run / execute 的完整三段式证据。

### 5. `tests/test_external_effects_mvp.py` 既有 questionnaire submit 400

多个 PR 中已记录该失败为 clean main 既有失败，不应归因于当前 Internal Event
迁移。它仍需要单独修复，尤其是在 questionnaire webhook / External Effects P1 前。

## 六、P1 推荐路线

建议按以下顺序推进：

1. 加 External Effects 防回潮 guard。
2. 补 payment automation 自然样本自动执行证据。
3. 逐个事件族开启低风险 consumer pair，优先 projection / audit / no-op 类消费者。
4. 增强事件中心运维页面：pair allowlist 状态、blocked reason、attempt delta、
   redaction preview、trace 外部效果关联。
5. 再考虑真实 webhook / WeCom / Feishu / broadcast execution 灰度。

P1 的每一步都应继续遵守：

- preview before execute
- batch size 1
- no force
- External Effects disabled unless该阶段专门审批真实执行
- event_type + consumer pair 精确 allowlist
- 每次只开一个事件族或一个 consumer pair

## 七、最终验收命令 / 检查清单

Set:

```bash
BASE_URL=https://www.youcangogogo.com
```

Health:

```bash
curl -sS -D /tmp/aicrm_health_headers.txt "$BASE_URL/health" -o /tmp/aicrm_health.json
rg -i 'HTTP/|x-aicrm-route-owner|x-aicrm-release-sha' /tmp/aicrm_health_headers.txt
jq '{ok, service, runtime_owner, legacy_runtime_enabled, production_data_ready}' /tmp/aicrm_health.json
```

Internal Events diagnostics:

```bash
curl -sS "$BASE_URL/api/admin/internal-events/diagnostics" | jq '{
  internal_events_enabled,
  payment_internal_events_enabled,
  questionnaire_internal_events_enabled,
  customer_tags_internal_events_enabled,
  customer_identity_internal_events_enabled,
  ai_campaign_internal_events_enabled,
  ops_plan_internal_events_enabled,
  broadcast_task_internal_events_enabled,
  owner_migration_internal_events_enabled,
  allowed_event_types,
  allowed_event_consumers,
  pair_allowlist_enabled,
  config_warnings,
  failed_terminal_count,
  stale_lock_count,
  blocked_by_pair_allowlist_count,
  due_count,
  real_external_call_executed
}'
```

External Effects diagnostics:

```bash
curl -sS "$BASE_URL/api/admin/external-effects/diagnostics" | jq '{
  real_execution_enabled,
  execution_mode,
  allowed_effect_types,
  real_external_call_executed,
  counts
}'
```

Allowed event types must include:

```text
payment.succeeded
questionnaire.submitted
customer.tagged
customer.untagged
customer.phone_bound
ai_campaign.created
ai_campaign.approved
ai_campaign.started
ops_plan.approved
broadcast_task.created
owner_migration.executed
```

Allowed event consumers must be payment-only:

```text
payment.succeeded:order_projection_consumer
payment.succeeded:customer_business_summary_consumer
payment.succeeded:dnd_policy_consumer
payment.succeeded:ai_assist_notify_consumer
payment.succeeded:automation_payment_consumer
```

Failed-terminal and stale-lock check: run the equivalent read-only aggregate in
the private ops environment. Do not publish production SQL bridge commands in
this repo.

External Effect attempt delta:

```bash
curl -sS "$BASE_URL/api/admin/external-effects/diagnostics" | jq '.counts'
```

Expected:

- `real_external_call_executed=false`
- `real_execution_enabled=false`
- `allowed_effect_types=[]`
- no unexpected `external_effect_attempt` increase during Internal Event checks

Selected event counts: collect the equivalent read-only aggregate from the
private ops environment, limited to the approved event types listed above.

Optional run-due preview with `automation_worker` JWT (`audience=internal_worker`, `scope=write`; see [`../auth_client_credentials.md`](../auth_client_credentials.md)):

```bash
curl -sS -X POST "$BASE_URL/api/admin/internal-events/run-due/preview" \
  -H "Authorization: Bearer $AICRM_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "batch_size": 1,
    "event_types": ["owner_migration.executed"],
    "consumer_names": [
      "customer_owner_projection_consumer",
      "customer_summary_mark_dirty_consumer",
      "owner_migration_ai_assist_notify_consumer",
      "webhook_owner_migration_consumer",
      "ai_assist_notify_consumer"
    ]
  }' | jq .
```

Expected for non-payment event families while pair allowlist is payment-only:

- `candidate_count=0`
- `event_consumers=[]`
- no new attempt
- no status change
- `real_external_call_executed=false`

## 八、PR / GitHub evidence index

| PR | Scope | Evidence summary |
|---|---|---|
| #1262 | `payment.succeeded` production fail/pass | Payment event family initial production verification and remediation trail. |
| #1264 | Single-consumer endpoint | Added per-event per-consumer dry-run / execute endpoint used for Q2 gray checks. |
| #1265 | Payment prep | Prepared payment worker gray path and safety gates. |
| #1266 | Payment transaction visibility | Improved payment transaction visibility for production diagnosis. |
| #1268 | Pair allowlist | Introduced / validated payment worker allowlist expansion path. |
| #1269 | `questionnaire.submitted` | Questionnaire shadow emit, fan-out, redaction, and no-real-webhook verification. |
| #1270 | `customer.tagged` / `customer.untagged` | Customer tag shadow event vertical slice. |
| #1271 | Pair-aware allowlist | Production-safe event_type:consumer_name allowlist behavior. |
| #1272 | `customer.phone_bound` | Customer identity phone-bound event slice. |
| #1273 | `customer.phone_bound` follow-up | Production validation / hardening for phone-bound event. |
| #1274 | `ai_campaign.created/approved/started` | AI campaign lifecycle event family. |
| #1275 | `ops_plan.approved` | Ops-plan approved event slice. |
| #1276 | `ops_plan.approved` follow-up | Production validation / legacy alias compatibility. |
| #1278 | `broadcast_task.created` | Broadcast task created event slice. |
| #1279 | `broadcast_task.created` hardening | Broadcast event production safety and payload handling. |
| #1281 | `broadcast_task.created` redaction | Stored payload/list/detail redaction hardening. |
| #1283 | `broadcast_task.created` trace lookup | Safe trace/original trace lookup without raw identifier exposure. |
| #1285 | `broadcast_task.created` production verification | Broadcast task event production verification. |
| #1286 | `broadcast_task.created` closeout | Broadcast task event final hardening / compatibility. |
| #1288 | `owner_migration.executed` | Owner migration event vertical slice and production WARN verification. |
| #1290 | `owner_migration.executed` count semantics | Count semantics hardening; production WARN verification comment `4707846493`. |

## Architecture Boundary

- Capability owner: `aicrm_next/platform_foundation/internal_events` plus the
  owning write path for each event family.
- Routes involved: `/health`, `/api/admin/internal-events/*`,
  `/api/admin/external-effects/diagnostics`, and the event-family write routes.
- Runtime owner: AI-CRM Next. No legacy runtime expansion is authorized.
- External calls: not authorized by this document.
- Production data: verification may read production diagnostics and event rows,
  but this document does not authorize writes or migrations.
- Fixture risk: do not treat local fixture evidence as production canary
  evidence.
- Rollback: disable the affected event-family flag and remove its event type
  from `AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES`; keep payment-only pair
  allowlist unless the incident is in payment itself.
