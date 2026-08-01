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
  - sender_userid: QianLan
    display_name: QianLan
    priority: 2
    status: active
---

# 业务说明

识别提交指定问卷且已经添加企业微信的用户。每 3 分钟刷新一次，新增用户将外推 external_userid 数组。

# Incremental SQL

```sql
SELECT
  'external_userid' AS identity_type,
  qs.external_userid AS identity_value,
  'questionnaire_submission:' || qs.submission_id::text AS event_source_key,
  jsonb_build_object(
    'questionnaire_id', qs.questionnaire_id,
    'submission_id', qs.submission_id,
    'submitted_at', qs.submitted_at
  ) AS payload_json,
  qs.external_userid,
  qs.submitted_at AS event_at
FROM audience_read.questionnaire_submissions_v1 qs
JOIN audience_read.wecom_contacts_v1 wc
  ON wc.external_userid = qs.external_userid
WHERE qs.questionnaire_id = :questionnaire_id
  AND qs.submitted_at >= :last_watermark_at - (:lookback_seconds || ' seconds')::interval
  AND qs.submitted_at < :refresh_started_at
```
