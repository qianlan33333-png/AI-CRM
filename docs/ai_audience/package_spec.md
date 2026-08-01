# AI Audience Markdown Package Spec

Markdown spec 是高级运行时输入，不是业务包代码提交格式。普通业务人群包不得通过 PR 新增；PR 只用于新增底层 `audience_read.*` view、平台能力或安全边界。Codex 可以临时生成 Markdown spec 并通过 External API dry-run/apply/publish，但不需要把每个业务包的 `.md` 文件提交到 repo。仓库里的 examples 只是模板。

普通“只按 external_userid 圈人”的包优先使用 Simple SQL API：

- `POST /api/external/ai-audience/simple/preview`
- `POST /api/external/ai-audience/simple/apply`
- `POST /api/external/ai-audience/simple/{package_key}/activate`
- `POST /api/external/ai-audience/simple/{package_key}/archive`

复杂包才使用 Markdown spec API：

- `POST /api/external/ai-audience/spec/dry-run`
- `POST /api/external/ai-audience/spec/apply`
- `POST /api/external/ai-audience/spec/publish`
- `POST /api/external/ai-audience/packages/{package_key}/archive`

Spec 使用 YAML frontmatter 描述包配置，用 Markdown SQL block 描述版本 SQL。

````markdown
---
package_key: q101_submitted_added_wecom
name: 提交 101 问卷且已加微
status: paused
query_mode: incremental_event
identity_policy: external_userid
refresh_mode: incremental_3m
natural_language_definition: 提交了 101 问卷，且已经添加企业微信的用户。
parameters:
  questionnaire_id: 101
automation_binding:
  agent_code: questionnaire_activation_agent
senders:
  - sender_userid: HuangYouCan
    display_name: HuangYouCan
    priority: 1
    status: active
---

# 业务说明

用于识别提交 101 问卷且已经加微的用户。

# Incremental SQL

```sql
SELECT
  'external_userid' AS identity_type,
  qs.external_userid AS identity_value,
  'questionnaire_submission:' || qs.submission_id::text AS event_source_key,
  jsonb_build_object('questionnaire_id', qs.questionnaire_id) AS payload_json,
  qs.external_userid,
  qs.submitted_at AS event_at
FROM audience_read.questionnaire_submissions_v1 qs
JOIN audience_read.wecom_contacts_v1 wc
  ON wc.external_userid = qs.external_userid
WHERE qs.questionnaire_id = :questionnaire_id
  AND qs.submitted_at >= :last_watermark_at - (:lookback_seconds || ' seconds')::interval
  AND qs.submitted_at < :refresh_started_at
```
````

## 必填 Frontmatter

- `package_key`
- `name`
- `refresh_mode`
- `natural_language_definition`

可选的 `automation_binding.agent_code` 使用自动化列表里的稳定 Agent code 建立一对一绑定。旧 `webhook`、地址、headers 与 payload template 配置均已退役，校验会明确返回 `webhook_configuration_retired`。

## Refresh Mode

只允许：

- `manual`
- `incremental_3m`
- `daily_0200`
- `incremental_3m_plus_daily_0200`

禁止 `incremental_5m`、5/15/30 分钟、cron、自定义时间。

## SQL 要求

- `incremental_3m` 必须有 `# Incremental SQL`。
- `daily_0200` 必须有 `# Snapshot SQL`。
- SQL 必须只查询 `audience_read.*` 白名单视图。
- 必须输出 `identity_type`、`identity_value`、`event_source_key`、`payload_json`。
- 推荐输出 `external_userid` 和 `event_at`。
- 禁止 `SELECT *`、DML/DDL、`public.*`、`pg_sleep`。
- 所有业务参数必须在 `parameters` 声明。

## 应用脚本

脚本只是调用运行时 API 或后台 Admin API 的辅助工具，不是让业务包进入 repo 的要求。Dry-run 是默认行为：

```bash
python scripts/ai_audience_apply_package_spec.py docs/ai_audience/examples/questionnaire_submitted_added_wecom.md --dry-run
```

创建或更新但不发布：

```bash
python scripts/ai_audience_apply_package_spec.py docs/ai_audience/examples/questionnaire_submitted_added_wecom.md --apply
```

通过生产 Admin API 时必须显式确认：

```bash
AICRM_ADMIN_SESSION_COOKIE='...' \
python scripts/ai_audience_apply_package_spec.py docs/ai_audience/examples/questionnaire_submitted_added_wecom.md \
  --api-base https://www.youcangogogo.com \
  --admin-session-cookie-from-env \
  --apply \
  --confirm-production
```

## Simple SQL 对照

Simple SQL 请求只提交业务 SQL，平台自动编译成内部标准 SQL：

```sql
WITH simple_audience AS (
  <user_sql>
)
SELECT
  'external_userid' AS identity_type,
  external_userid AS identity_value,
  'simple:' || :package_key || ':' || external_userid AS event_source_key,
  '{}'::jsonb AS payload_json,
  external_userid,
  CAST(:refresh_started_at AS timestamptz) AS event_at
FROM simple_audience
WHERE external_userid IS NOT NULL
```

Simple SQL 只允许 `every_3m`、`daily_0200`、`manual`。业务参数必须声明；`package_key`、`package_id`、`refresh_started_at`、`last_watermark_at`、`lookback_seconds` 由系统注入。

示例：“HuangYouCan 企微未注册人群”应使用通用 view 表达，不再写专用迁移或 seed 代码：

```sql
SELECT DISTINCT wc.external_userid
FROM audience_read.wecom_contacts_v1 wc
LEFT JOIN audience_read.registration_status_v1 r
  ON r.external_userid = wc.external_userid
WHERE wc.owner_userid = :owner_userid
  AND COALESCE(r.is_registered, false) = false
```
