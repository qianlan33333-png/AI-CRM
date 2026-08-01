# AI 自动化运营人群包配置说明书

> 适用对象：负责创建人群包的 Agent、配置和启用人群包的运营人员。
> 能力归属：`ai_audience_ops`；路由和页面全部属于 AI-CRM Next。
> 安全原则：调用方只提交模板和业务字段；接口、管理页和本文档都不提供 SQL。

## 1. 强制执行流程

Agent 必须按下面顺序工作：

1. 从本说明书选择一个固定 `template_key`，填写业务参数。
2. 调用 `POST /api/external/ai-audience/templates/preview`。
3. 检查规范化参数、自然语言规则、依赖、风险提示、命中人数和脱敏样本。
4. 如果用户明确说“只预览”，到此停止，绝对不得调用 Apply。
5. 否则，Preview 成功且命中人数非零后，默认立即用相同请求调用 `POST /api/external/ai-audience/templates/apply`。
6. Apply 只创建或更新 `paused` 人群包；不得调用旧的 activate/publish 接口。
7. 运营人员进入二级详情页复核、修正模板参数、绑定自动化和发送人，最后人工启用。

零人群不会被默认创建。只有业务方明确确认后，才可令 `allow_empty=true` 再 Apply。

没有“普通模式/专家模式”。旧 Simple SQL 和 Markdown Spec 接口只是底层兼容能力，Agent 不应调用。

## 2. 认证与接口

External Agent 使用 API Client JWT：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

Token 必须具备 `external_write` capability 和 `write` scope。`package_key` 继续受生产 prefix gate 管理；不符合环境允许前缀时返回 `unsafe_package_key_prefix`。

统一接口：

| 场景 | 方法与路径 | 是否写库 |
| --- | --- | --- |
| Agent 预览 | `POST /api/external/ai-audience/templates/preview` | 否；只读查询与审计 |
| Agent 创建/更新 | `POST /api/external/ai-audience/templates/apply` | 是；单事务，结果保持 paused |
| 后台读取模板 | `GET /api/admin/ai-audience/templates` | 否 |
| 后台重新预览 | `POST /api/admin/ai-audience/packages/{package_id}/template-preview` | 否 |
| 后台保存新版本 | `PUT /api/admin/ai-audience/packages/{package_id}/template-config` | 是；活动包拒绝修改 |

## 3. 统一请求结构

Preview 与 Apply 使用同一结构；未知字段一律拒绝。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `package_key` | string | 是 | 稳定人群包 key，执行 prefix gate |
| `name` | string | 是 | 运营可读名称 |
| `template_key` | string | 是 | 本文六个固定模板之一 |
| `template_version` | integer | 否 | 省略时使用注册表当前版本；当前均为 `1` |
| `parameters` | object | 是 | 模板业务字段；禁止 SQL、字段名或表名 |
| `refresh_mode` | enum | 否 | `manual`、`every_3m`、`daily_0200`、`every_3m_plus_daily_0200`；省略采用模板默认值 |
| `senders` | array | 否 | 最多 5 个发送人对象 |
| `group_name` | string | 否 | 只精确解析已有分组；External Agent 不创建分组 |
| `automation_agent_code` | string | 否 | 精确绑定已有且可用的自动化 Agent |
| `allow_empty` | boolean | 否 | 默认 `false`；零人群确认开关 |
| `operator` | string | 否 | 操作人/Agent 标识，写审计 |

发送人对象字段：

| 字段 | 类型 | 默认 | 约束 |
| --- | --- | --- | --- |
| `sender_userid` | string | 无 | 必填、同一请求内唯一 |
| `display_name` | string | `sender_userid` | 仅用于后台展示 |
| `priority` | integer | `100` | 1—10000 |
| `status` | enum | `active` | `active` 或 `paused` |

所有模板都包含负责人字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `owner_scope` | enum | 是 | 必须显式为 `specified` 或 `all` |
| `owner_userids` | string[] | 条件必填 | `owner_scope=specified` 时至少 1 个；`all` 时必须为空或省略 |

## 4. Preview 响应怎么读

成功响应包含：

- `template_key`、`template_version`：实际采用的不可变模板版本。
- `normalized_parameters`：人类引用解析后的稳定 ID/code 和规范化时间。
- `natural_language_rule`：服务端根据最终参数生成的业务解释。
- `dependencies`：只读投影依赖，不包含 SQL。
- `matched_count`：最多统计 10,000。
- `matched_count_is_lower_bound=true` 和 `matched_count_display="至少 10,000 人"`：命中超过统计上限。
- `sample_rows`：最多 10 条，只含不可逆脱敏身份摘要。
- `risk_warnings`：例如 `empty_audience`、`count_capped_at_10000`。
- `template_fingerprint`：幂等判断摘要，不是 SQL。

查询超时固定为 10 秒。`preview_timeout` 会阻断 Apply，Agent 不得绕过或改用旧 SQL 接口。

## 5. 六类模板

### 5.1 `wecom_contact_registration`

适用：按企微联系人状态和产品注册状态圈选。
不适用：按聊天文本、任意标签表达式或自定义条件树圈选。

默认刷新：`every_3m`。

| 参数 | 类型 | 默认 | 可选值/说明 |
| --- | --- | --- | --- |
| `owner_scope` | enum | 无 | `specified` / `all` |
| `owner_userids` | string[] | `[]` | 指定负责人 |
| `contact_statuses` | enum[] | `["active"]` | `active`、`deleted` |
| `registration_status` | enum | `any` | `any`、`registered`、`unregistered` |

```json
{
  "package_key": "official_unregistered_contacts",
  "name": "企微有效联系人未注册",
  "template_key": "wecom_contact_registration",
  "parameters": {
    "owner_scope": "specified",
    "owner_userids": ["HuangYouCan"],
    "contact_statuses": ["active"],
    "registration_status": "unregistered"
  },
  "refresh_mode": "every_3m",
  "operator": "audience_agent"
}
```

### 5.2 `questionnaire_choice_answers`

适用：一个问卷的首次完整提交，按多个选择题答案组合圈选。
不适用：文本题、手机号题、分值判断、第二次或后续提交、任意跨题 OR。

规则固定为：多个题目条件之间 AND；同一题多个候选选项之间 OR。支持 `single_choice` 和 `multi_choice`；多选题只判断是否命中任一候选选项。

默认刷新：`every_3m`。

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `questionnaire` | reference | 无 | 问卷 ID、slug/code 或精确标题 |
| `conditions` | object[] | 无 | 至少一个 `{question, options}` |
| `conditions[].question` | reference | 无 | 题目 ID 或该问卷内精确标题 |
| `conditions[].options` | reference[] | 无 | 选项 ID 或该题内精确选项文本 |
| `owner_scope` | enum | 无 | `specified` / `all` |
| `owner_userids` | string[] | `[]` | 指定负责人 |

```json
{
  "package_key": "official_questionnaire_growth",
  "name": "问卷高意向成长用户",
  "template_key": "questionnaire_choice_answers",
  "parameters": {
    "questionnaire": "AI时代个人成长诊断",
    "conditions": [
      {"question": "你当前最大的挑战是？", "options": ["缺少稳定获客", "缺少产品化能力"]},
      {"question": "你愿意投入的时间？", "options": ["每天1小时以上"]}
    ],
    "owner_scope": "all",
    "owner_userids": []
  },
  "operator": "audience_agent"
}
```

### 5.3 `paid_order`

适用：按商品、支付时间窗口和负责人圈选已支付订单用户。
不适用：未支付、退款状态推断、金额表达式或任意订单 SQL。

支付窗口为左闭右开 `[paid_at_from, paid_at_to)`；起点和终点可单独省略。

默认刷新：`every_3m`。

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `products` | reference[] | 无 | 商品 ID、`product_code` 或精确商品名；至少 1 个 |
| `paid_at_from` | ISO datetime | 空 | 包含起点 |
| `paid_at_to` | ISO datetime | 空 | 不包含终点 |
| `owner_scope` | enum | 无 | `specified` / `all` |
| `owner_userids` | string[] | `[]` | 指定负责人 |
| `require_active_wecom_contact` | boolean | `true` | 是否要求有效企微联系人 |

```json
{
  "package_key": "official_paid_999_july",
  "name": "7月已支付999产品",
  "template_key": "paid_order",
  "parameters": {
    "products": ["999成长营"],
    "paid_at_from": "2026-07-01T00:00:00+08:00",
    "paid_at_to": "2026-08-01T00:00:00+08:00",
    "owner_scope": "all",
    "owner_userids": [],
    "require_active_wecom_contact": true
  },
  "operator": "audience_agent"
}
```

### 5.4 `channel_entry`

适用：按渠道和“距最后一次进入渠道”的天数窗口圈选。
不适用：任意事件路径、跨渠道 AND 或自定义日期计算。

时间窗口为 `[entered_days_min, entered_days_max)` 天；不填最大值表示至少最小天数。多个渠道为 OR。

默认刷新：`every_3m`。

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `channels` | reference[] | 无 | 渠道 ID、code 或精确中文名称；至少 1 个 |
| `entered_days_min` | integer | `0` | 最小经过天数，包含 |
| `entered_days_max` | integer | 空 | 最大经过天数，不包含且必须大于最小值 |
| `owner_scope` | enum | 无 | `specified` / `all` |
| `owner_userids` | string[] | `[]` | 指定负责人 |
| `require_active_wecom_contact` | boolean | `true` | 是否要求有效企微联系人 |

```json
{
  "package_key": "official_channel_day_3_7",
  "name": "渠道进入3至7天",
  "template_key": "channel_entry",
  "parameters": {
    "channels": ["视频号直播间", "公众号菜单"],
    "entered_days_min": 3,
    "entered_days_max": 7,
    "owner_scope": "specified",
    "owner_userids": ["HuangYouCan"],
    "require_active_wecom_contact": true
  },
  "operator": "audience_agent"
}
```

### 5.5 `radar_first_click_elapsed`

适用：按第一次可归因雷达点击距今时间圈选。
不适用：用最近一次点击重置计时、多个雷达 AND、不可归因匿名点击。

多个雷达为 OR；同一用户和雷达只取第一次可归因点击。窗口固定为 `[elapsed_min, elapsed_max)`；最大值可省略。

默认刷新：`every_3m`。

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `radars` | reference[] | 无 | 雷达 ID、code 或精确标题；至少 1 个 |
| `elapsed_min` | integer | `0` | 最小经过时间，包含 |
| `elapsed_max` | integer | 空 | 最大经过时间，不包含且必须大于最小值 |
| `elapsed_unit` | enum | `day` | `hour` 或 `day` |
| `owner_scope` | enum | 无 | `specified` / `all` |
| `owner_userids` | string[] | `[]` | 指定负责人 |

```json
{
  "package_key": "official_radar_24_72h",
  "name": "雷达首点24至72小时",
  "template_key": "radar_first_click_elapsed",
  "parameters": {
    "radars": ["AI自习室介绍", "999产品说明"],
    "elapsed_min": 24,
    "elapsed_max": 72,
    "elapsed_unit": "hour",
    "owner_scope": "all",
    "owner_userids": []
  },
  "operator": "audience_agent"
}
```

### 5.6 `member_usage_status`

适用：按服务期、注册、真实使用、会员层级和会员状态圈选。
不适用：把页面访问等弱行为当成真实使用，或自定义会员表关联。

默认刷新：`daily_0200`。

| 参数 | 类型 | 默认 | 可选值/说明 |
| --- | --- | --- | --- |
| `owner_scope` | enum | 无 | `specified` / `all` |
| `owner_userids` | string[] | `[]` | 指定负责人 |
| `service_period` | enum | `active` | `any`、`active`、`expired` |
| `registration_status` | enum | `any` | `any`、`registered`、`unregistered` |
| `usage_status` | enum | `any` | `any`、`used`、`unused` |
| `membership_tiers` | string[] | `[]` | 空数组代表全部会员层级 |
| `membership_statuses` | string[] | `[]` | 空数组代表全部会员状态 |

```json
{
  "package_key": "official_active_member_unused",
  "name": "服务期内已注册未真实使用",
  "template_key": "member_usage_status",
  "parameters": {
    "owner_scope": "specified",
    "owner_userids": ["HuangYouCan"],
    "service_period": "active",
    "registration_status": "registered",
    "usage_status": "unused",
    "membership_tiers": ["annual", "private_teacher"],
    "membership_statuses": ["active"]
  },
  "refresh_mode": "daily_0200",
  "operator": "audience_agent"
}
```

## 6. 人类可读引用解析规则

`questionnaire`、题目、选项、`products`、`channels` 和 `radars` 支持：

1. 稳定数字 ID；
2. 业务稳定 code/slug（对象具备时）；
3. 精确中文标题/名称。

服务端只做精确匹配，不做模糊猜测、同义词扩展或“最像的一个”。

- 没有匹配：`reference_not_found`，返回空候选或最多 10 个可操作信息。
- 精确标题重名：`reference_ambiguous`，返回最多 10 个候选；Agent 必须改用稳定 ID/code 后再次 Preview。
- 问卷题目只在指定问卷内解析；选项只在指定题目内解析。
- 问卷文本题/手机号题/评分题返回 `question_type_not_supported`。
- `group_name` 只解析已有分组；不存在返回 `group_not_found`，External Agent 不得创建。

## 7. Apply、paused、绑定、启用和发送的安全边界

- Preview 不创建 package/version，不刷新成员，不产生 member event，不触发 external effect。
- Apply 重新执行完整 Preview；10 秒超时或校验失败不会写入。
- Apply 在一个事务里处理 package、version、dependency、发送人、已有分组和可选自动化绑定；任一步失败全部回滚。
- Apply 成功后 package 固定为 `paused`，刷新调度时间为空。
- 模板版本可标记为已发布，但“版本已发布”不等于“人群包已启用”。
- `paused` 状态下不会刷新成员，不会产生进入/离开事件，不会发送消息。
- 绑定自动化和发送人只是配置；启用必须由运营人员在详情页人工确认。
- 真实群发仍需在列表页进入标准群发确认流程。
- 相同 `package_key` 和完全相同的模板指纹重复 Apply 会复用版本。
- 活动包的完全相同请求可幂等返回；任何名称、条件、刷新、分组、发送人或绑定规则变化返回 `active_package_update_requires_pause`。

## 8. 稳定错误码与 Agent 下一步动作

| 错误码 | 含义 | Agent 下一步 |
| --- | --- | --- |
| `invalid_request` | 请求结构、未知公共字段或发送人不合法 | 对照统一结构修正，不猜字段 |
| `unsafe_package_key_prefix` | package key 不符合环境前缀 | 使用获准前缀；不要绕过 |
| `template_not_found` | 模板 key 不存在 | 从本文六个 key 中重选 |
| `template_version_not_found` | 指定版本不存在 | 省略版本或使用目录返回版本 |
| `unknown_parameter` | parameters 含模板未知字段 | 删除未知字段后重试 |
| `parameter_required` | 必填模板字段为空 | 补齐字段 |
| `invalid_parameter_type` | 字段类型错误 | 按字段表改为正确 JSON 类型 |
| `invalid_parameter_value` | 枚举、数字或时间不合法 | 使用响应允许值修正 |
| `owner_scope_required` | 未明确负责人范围 | 明确填 `specified` 或 `all` |
| `owner_userids_required` | specified 未给负责人 | 填至少一个 UserID |
| `invalid_time_window` | 最大值不大于最小值或结束早于开始 | 修正为左闭右开有效窗口 |
| `reference_not_found` | ID/code/精确标题不存在 | 核对来源系统名称或改用稳定 ID |
| `reference_ambiguous` | 精确标题重名 | 从最多 10 个候选中选稳定 ID/code |
| `question_type_not_supported` | 使用了文本、手机号或其他非选择题 | 换成单选/多选题，或终止配置 |
| `group_not_found` | 已有分组不存在 | 让运营先在后台建组，或省略分组 |
| `automation_not_found` | Agent code 不存在 | 核对自动化 code 或省略绑定 |
| `automation_not_active` | 自动化不可新绑定 | 运营先启用自动化，之后重新 Apply |
| `automation_already_bound` | 自动化已绑定其他人群包 | 选择其他自动化或由运营解除旧绑定 |
| `invalid_sender_status` | 发送人状态不是 active/paused | 修正状态 |
| `preview_failed` | 只读预览执行失败 | 保留原请求和错误，交给系统排查 |
| `preview_timeout` | 预览超过 10 秒 | 停止 Apply，缩小业务范围或交给系统优化 |
| `apply_failed` | Apply 事务失败且已回滚 | 不要启用或重试外呼；保留请求并交给管理员排查 |
| `empty_audience_requires_confirmation` | 命中 0 人且未确认 | 向用户确认；获准后设 `allow_empty=true` |
| `active_package_update_requires_pause` | 活动包规则发生变化 | 让运营先停止包，再用相同 key Apply |
| `archived_package_cannot_update` | 包已归档 | 使用新 package key，或由运营恢复业务流程 |
| `template_sql_validation_failed` | 服务端模板编译契约异常 | 不绕过；记录 template key/version 交给研发 |

## 9. 运营页面确认清单

进入 `/admin/automation-conversion/packages/{package_id}` 后：

1. 确认页面只有一个人群包标题，没有重复说明。
2. 确认模板标签、版本和自然语言筛选逻辑正确。
3. 检查负责人范围是否明确；尤其注意 `all`。
4. 点击“重新预览”，核对人数、规则、风险提示和脱敏样本。
5. 若人数为 0，仅在业务确实需要空包时勾选确认。
6. 停止状态下才可“保存新版本”；活动状态模板表单应只读。
7. 核对所属分组、自动化绑定和发送人白名单。
8. 确认自动化本身的状态和话术；绑定不代表已发送。
9. 人工点击启用；启用后再观察首次刷新结果。
10. 群发必须另走列表页标准群发确认，不因模板保存自动发生。

## 10. 历史人群包转换

旧 SQL/Markdown 人群包在列表名称下显示“历史配置”，详情页也显示历史提示。

转换步骤：

1. 若历史包正在运行，先停止。
2. 保持原 `package_key` 不变。
3. 根据历史自然语言说明选择最接近的六类模板；不允许为了兼容旧逻辑而增加任意条件树。
4. 在详情页或由 Agent 填写模板字段并 Preview。
5. 核对新旧人数差异；需要保留差异原因。
6. 保存为新模板版本；原历史版本仍保留，不做破坏性迁移。
7. 重新核对绑定、发送人和刷新方式，再人工启用。

如果旧逻辑无法由六类模板准确表达，停止转换并提交模板扩展需求；不得退回自定义 SQL 作为新配置方式。
