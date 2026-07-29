# SQL Authoring Guide

AI Audience SQL 只能查询 `audience_read.*` 白名单视图。SQL linter 会拒绝 DML/DDL、危险函数、非白名单依赖和 `SELECT *`。

## 允许视图

- `audience_read.identity_universe_v1`
- `audience_read.questionnaire_submissions_v1`
- `audience_read.orders_v1`
- `audience_read.wecom_contacts_v1`
- `audience_read.registration_status_v1`
- `audience_read.huangyoucan_member_usage_status_v1`
- `audience_read.channel_entries_v1`
- `audience_read.group_chat_members_v1`

## 群成员包

`audience_read.group_chat_members_v1` 只暴露企微客户群里的外部联系人，
可用 `chat_id` 精确筛选指定群。典型用法是 `refresh_mode: manual` +
`query_mode: snapshot_current`，创建后按需手动刷新，不进入定时刷新队列。

该视图只读取当前 `group_chats.raw_payload` 里的群详情缓存；群成员变化后的
缓存更新机制由 group ops / 数据同步链路负责，不在 AI Audience 包内新增。

## 黄小璨会员/使用状态

`audience_read.huangxiaocan_member_usage_status_v1` 汇总黄小璨会员命中、
注册身份命中和真实使用记录。普通业务包只需要通过 Simple SQL 引用该视图，
不要为单个人群包提交代码或 Markdown spec。该视图读取原子切换的 active generation
投影，不在业务查询中重新扫描会员、注册和消息源表。

示例：

```sql
SELECT DISTINCT external_userid
FROM audience_read.huangxiaocan_member_usage_status_v1
WHERE owner_userid = :owner_userid
  AND is_member = true
  AND is_registered = true
  AND has_real_usage = false
```

## 必填列

每条 SQL 必须输出：

- `identity_type`
- `identity_value`
- `event_source_key`
- `payload_json`

推荐同时输出：

- `external_userid`
- `event_at`

## 系统参数

系统会提供：

- `:last_watermark_at`
- `:refresh_started_at`
- `:lookback_seconds`
- `:package_id`

业务参数必须在 spec 的 `parameters` 中声明。

## 性能要求

增量 SQL 应限制在 watermark 和 refresh window 内；每日快照 SQL 应优先通过业务时间、状态和已加微关系收敛扫描范围。
