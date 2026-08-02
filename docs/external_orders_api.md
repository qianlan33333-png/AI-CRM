# 外部只读 API

## 概述

这组接口给外部系统只读拉取订单、用户基础信息和问卷提交数据。

接口只查询本地读模型，不会创建订单、发起支付、发起退款、主动同步微信小店订单，也不会触发企微、webhook 或自动化发送。

## 生产访问地址

生产环境 Base URL：

```text
https://www.youcangogogo.com
```

当前文档涉及的完整接口地址：

| 能力 | Method | 完整 URL |
|---|---|---|
| 查询用户基础信息 | `GET` | `https://www.youcangogogo.com/api/external/users/resolve` |
| 查询问卷提交 | `GET` | `https://www.youcangogogo.com/api/external/questionnaire-submissions` |
| 查询聊天记录 | `GET` | `https://www.youcangogogo.com/api/external/chat-records` |
| 批量查询订单 | `GET` | `https://www.youcangogogo.com/api/external/orders` |
| 查询订单详情 | `GET` | `https://www.youcangogogo.com/api/external/orders/{order_no}` |

可选 shell 变量写法：

```bash
BASE_URL="https://www.youcangogogo.com"
CRM_API_KEY="<后台仅展示一次的唯一 API Key>"
```

## 鉴权

### 获取唯一 API Key

配置管理员或超级管理员进入后台「系统配置 → CRM 开放 API Key」（`/admin/config/api-key`），点击“生成并启用 API Key”。Key 只显示一次，复制后即可直接调用，无需 SSH、Client ID 或 `/oauth/token`。

该唯一 Key 固定只有 `read` scope 与 `external_read` capability，不能调用任何写接口。遗失时重新生成；重新生成后旧 Key 立即失效。

所有请求都在 Header 中直接携带唯一 Key：

```http
Authorization: Bearer <CRM_API_KEY>
```

完整的生成、轮换与停用规则见 [`auth_client_credentials.md`](auth_client_credentials.md)。聊天、订单、用户和问卷接口均校验 `external_read` capability，不接受运行环境共享 Token。

错误语义：

| 场景 | HTTP 状态 | error_code |
|---|---:|---|
| 请求未带 Token | `401` | `access_token_required` |
| Token 错误或签名无效 | `401` | `invalid_access_token` |
| Key 已停用 | `401` | `client_disabled` |
| audience、scope 或 capability 越权 | `403` | `invalid_target` / `scope_or_capability_required` |

## 查询用户基础信息

```http
GET /api/external/users/resolve
```

按用户身份键解析用户基础信息。第一版只读本地身份和客户读模型，不会主动同步企微联系人。

### Query 参数

至少传入以下参数之一。建议订单侧优先使用 `unionid`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---:|---|---|
| `unionid` | string | 否 | - | 微信 unionid |
| `external_userid` | string | 否 | - | 企业微信外部联系人 ID |
| `mobile` | string | 否 | - | 手机号 |
| `openid` | string | 否 | - | 微信 openid |

### 请求示例

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/users/resolve?unionid=orSqJ5iT9UoeYQRVxvAoo_8avkmA"
```

### 响应字段

| 字段 | 说明 |
|---|---|
| `ok` | 是否成功 |
| `user` | 用户基础信息 |
| `route_owner` | 路由归属 |
| `source_status` | 数据来源标记，固定为 `external_user_basic` |
| `fallback_used` | 是否走 fallback |

`user` 固定包含 `mobile`、`customer_name`、`unionid` 三个字段；未解析到具体值时返回空字符串。

| user 字段 | 说明 |
|---|---|
| `person_id` | 系统内部用户 ID |
| `external_userid` | 企业微信外部联系人 ID |
| `mobile` | 手机号 |
| `customer_name` | 客户名称 |
| `unionid` | 微信 unionid |
| `openid` | 微信 openid |
| `owner_userid` | 当前负责人 userid |
| `owner_display_name` | 当前负责人展示名 |
| `remark` | 客户备注 |
| `follow_user_userid` | 身份映射里的跟进员工 userid |
| `follow_user_userids` | 客户详情里的跟进员工 userid 数组 |
| `binding_status` | 绑定状态 |
| `is_bound` | 是否已绑定手机号或身份 |
| `matched_by` | 本次匹配使用的键：`unionid`、`external_userid`、`mobile`、`openid` |
| `identity_map_id` | 身份映射表 ID |
| `detail_url` | 内部客户详情 API 路径 |

响应示例：

```json
{
  "ok": true,
  "user": {
    "person_id": "123",
    "external_userid": "wm_xxx",
    "mobile": "13800138000",
    "customer_name": "张三",
    "unionid": "orSqJ5iT9UoeYQRVxvAoo_8avkmA",
    "openid": "o_xxx",
    "owner_userid": "zhangsan",
    "owner_display_name": "张三",
    "remark": "张三妈妈",
    "follow_user_userid": "zhangsan",
    "follow_user_userids": ["zhangsan"],
    "binding_status": "bound",
    "is_bound": true,
    "matched_by": "unionid",
    "identity_map_id": 123,
    "detail_url": "/api/customers/wm_xxx"
  },
  "route_owner": "ai_crm_next",
  "source_status": "external_user_basic",
  "fallback_used": false
}
```

## 查询聊天记录

```http
GET /api/external/chat-records
```

按用户身份键查询本地归档聊天记录。该接口使用统一 CRM 开放 API Key，只读本地 `archived_messages`/消息读模型，不会实时调用企微，也不会发送消息或触发自动化。

### Query 参数

`mobile`、`unionid`、`external_userid` 至少传一个。`start_time` 和 `chat_scene` 必传。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---:|---|---|
| `mobile` | string | 条件必填 | - | 手机号 |
| `unionid` | string | 条件必填 | - | 微信 unionid |
| `external_userid` | string | 条件必填 | - | 企业微信外部联系人 ID |
| `start_time` | integer | 是 | - | 聊天记录起始时间，秒级 Unix 时间戳 |
| `chat_scene` | string | 是 | - | 聊天场景：`private`/`私信` 或 `group`/`群聊` |
| `with_userid` | string | 否 | `HuangYouCan` | 私信场景下查询和哪个员工的聊天记录；空值默认 `HuangYouCan` |
| `cursor` | string | 否 | - | 下一页游标，使用上一页返回的 `next_cursor` |

分页固定每页 `20` 条。首次请求不传 `cursor`；如果 `has_more=true`，下一页保持原筛选条件不变并追加 `cursor`。

### 请求示例

按手机号查询与 `HuangYouCan` 的私信记录：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/chat-records?mobile=13800138000&start_time=1780272000&chat_scene=private"
```

按 unionid 查询与指定员工的私信记录：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/chat-records?unionid=orSqJ5iT9UoeYQRVxvAoo_8avkmA&start_time=1780272000&chat_scene=private&with_userid=ZhaoYanFang"
```

按 external_userid 查询群聊记录：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/chat-records?external_userid=wm_xxx&start_time=1780272000&chat_scene=group"
```

拉取下一页：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/chat-records?mobile=13800138000&start_time=1780272000&chat_scene=private&cursor=xxxx"
```

### 响应字段

| 字段 | 说明 |
|---|---|
| `ok` | 是否成功 |
| `items` / `messages` | 聊天记录数组 |
| `total` | 当前筛选条件下的总数 |
| `count` | 当前页返回条数 |
| `limit` | 固定为 `20` |
| `next_cursor` | 下一页游标；为空表示没有下一页 |
| `has_more` | 是否还有下一页 |
| `external_userid` | 最终解析出的企业微信外部联系人 ID |
| `matched_by` | 本次匹配使用的键：`external_userid`、`unionid` 或 `mobile` |
| `filters` | 服务端实际使用的筛选条件 |
| `route_owner` | 路由归属，固定为 `ai_crm_next` |
| `source_status` | 固定为 `external_chat_records` |
| `read_model_status` | 读模型状态 |
| `fallback_used` | 固定为 `false` |

`items[]` 字段：

| 字段 | 说明 |
|---|---|
| `msgid` | 消息 ID |
| `chat_scene` | 标准化聊天场景：`private` 或 `group` |
| `chat_type` | 原始聊天类型 |
| `external_userid` | 企业微信外部联系人 ID |
| `with_userid` | 员工 userid；私信场景通常是本次对话员工 |
| `sender` | 发送方 |
| `receiver` | 接收方 |
| `chat_id` | 群聊 ID；私信为空 |
| `roomid` | 群聊 roomid；私信为空 |
| `group_name` | 群名；私信为空 |
| `msgtype` | 消息类型 |
| `content` | 消息内容 |
| `send_time` | 发送时间 |
| `source_id` | 本地归档行 ID |

响应示例：

```json
{
  "ok": true,
  "items": [
    {
      "msgid": "msg_001",
      "chat_scene": "private",
      "chat_type": "single",
      "external_userid": "wm_xxx",
      "with_userid": "HuangYouCan",
      "sender": "wm_xxx",
      "receiver": "HuangYouCan",
      "chat_id": "",
      "roomid": "",
      "group_name": "",
      "msgtype": "text",
      "content": "我刚买了 9.9，想知道第一步怎么做",
      "send_time": "2026-06-14T09:20:30+00:00",
      "source_id": "123"
    }
  ],
  "messages": [
    {
      "msgid": "msg_001",
      "chat_scene": "private",
      "chat_type": "single",
      "external_userid": "wm_xxx",
      "with_userid": "HuangYouCan",
      "sender": "wm_xxx",
      "receiver": "HuangYouCan",
      "chat_id": "",
      "roomid": "",
      "group_name": "",
      "msgtype": "text",
      "content": "我刚买了 9.9，想知道第一步怎么做",
      "send_time": "2026-06-14T09:20:30+00:00",
      "source_id": "123"
    }
  ],
  "total": 1,
  "count": 1,
  "limit": 20,
  "next_cursor": "",
  "has_more": false,
  "external_userid": "wm_xxx",
  "matched_by": "mobile",
  "filters": {
    "chat_scene": "private",
    "start_time": "2026-06-01 00:00:00",
    "with_userid": "HuangYouCan"
  },
  "route_owner": "ai_crm_next",
  "source_status": "external_chat_records",
  "read_model_status": "primary",
  "fallback_used": false
}
```

## 查询问卷提交

```http
GET /api/external/questionnaire-submissions
```

按用户身份键查询问卷提交和答案快照。该接口与订单 API 使用同一个 CRM 开放 API Key，只读本地问卷提交读模型，不会触发外部推送、企微打标、支付或自动化动作。

### Query 参数

`mobile`、`unionid`、`external_userid` 至少传一个。其余参数可选。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---:|---|---|
| `mobile` | string | 条件必填 | - | 手机号 |
| `unionid` | string | 条件必填 | - | 微信 unionid |
| `external_userid` | string | 条件必填 | - | 企业微信外部联系人 ID |
| `questionnaire_id` | integer | 否 | - | 指定某份问卷 |
| `submitted_from` | integer | 否 | - | 提交开始时间，秒级 Unix 时间戳 |
| `submitted_to` | integer | 否 | - | 提交结束时间，秒级 Unix 时间戳 |
| `limit` | integer | 否 | `100` | 每页条数，最大 `500` |
| `cursor` | string | 否 | - | 下一页游标，使用上一页返回的 `next_cursor` |

### 请求示例

按真实付费用户手机号查询最近 5 条问卷提交：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/questionnaire-submissions?mobile=13800138000&limit=5"
```

按 unionid + 指定问卷查询：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/questionnaire-submissions?unionid=orSqJ5iT9UoeYQRVxvAoo_8avkmA&questionnaire_id=21&limit=5"
```

按时间窗口查询：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/questionnaire-submissions?mobile=13800138000&submitted_from=1780272000&submitted_to=1781222399&limit=20"
```

### 响应字段

| 字段 | 说明 |
|---|---|
| `ok` | 是否成功 |
| `items` | 问卷提交数组 |
| `total` | 当前筛选条件下的总数 |
| `limit` | 当前页大小 |
| `next_cursor` | 下一页游标；为空表示没有下一页 |
| `has_more` | 是否还有下一页 |
| `filters` | 服务端实际使用的筛选条件 |
| `route_owner` | 路由归属，固定为 `ai_crm_next` |
| `source_status` | 固定为 `external_questionnaire_submissions` |
| `read_model_status` | 读模型状态 |
| `fallback_used` | 固定为 `false` |

`items[]` 提交级字段：

| 字段 | 说明 |
|---|---|
| `mobile` | 手机号 |
| `unionid` | 微信 unionid |
| `external_userid` | 企业微信外部联系人 ID |
| `submitted_at` | 提交时间 |
| `questionnaire_id` | 问卷 ID |
| `questionnaire_title` | 问卷标题快照 |
| `final_tags` | 本次问卷计算出的最终标签数组 |
| `assessment_result_snapshot` | 测评结果快照对象 |
| `answers` | 答案数组 |

`answers[]` 答案级字段：

| 字段 | 说明 |
|---|---|
| `question_title_snapshot` | 问题标题快照 |
| `selected_option_texts_snapshot` | 选项文本快照数组 |
| `text_value` | 文本答案；选择题的其他填写内容也在这里 |
| `score_contribution` | 本题分数贡献 |

响应示例：

```json
{
  "ok": true,
  "items": [
    {
      "mobile": "13800138000",
      "unionid": "orSqJ5iT9UoeYQRVxvAoo_8avkmA",
      "external_userid": "wm_xxx",
      "submitted_at": "2026-06-14T09:20:30+00:00",
      "questionnaire_id": 21,
      "questionnaire_title": "首月体验问卷",
      "final_tags": ["tag_interest_ai_tools"],
      "assessment_result_snapshot": {
        "level": "starter"
      },
      "answers": [
        {
          "question_title_snapshot": "你现在卡在哪里？",
          "selected_option_texts_snapshot": ["不知道怎么开始"],
          "text_value": "",
          "score_contribution": 3.0
        },
        {
          "question_title_snapshot": "你最想要什么帮助？",
          "selected_option_texts_snapshot": [],
          "text_value": "希望有人帮我拆第一步",
          "score_contribution": 0.0
        }
      ]
    }
  ],
  "total": 1,
  "limit": 5,
  "next_cursor": "",
  "has_more": false,
  "filters": {
    "mobile": "13800138000"
  },
  "route_owner": "ai_crm_next",
  "source_status": "external_questionnaire_submissions",
  "read_model_status": "primary",
  "fallback_used": false
}
```

## 当前产品编码对照

以下对照来自当前生产商品列表 `GET /api/admin/wechat-pay/products?limit=100`，用于 `product_code` 参数。

| product_code | 产品名称 | 金额 | 状态 | 备注 |
|---|---|---:|---|---|
| `premium_monthly_trial` | 黄小璨月度会员私教版 | 69.00 | active | 当前可用 |
| `subscription_monthly` | 黄小璨订阅版-月付 | 19.90 | active | 当前可用 |
| `subscription_trial_month` | 黄小璨首月体验 | 9.90 | active | 当前可用 |
| `prd_20260528102810_0fe9ac` | 黄小璨私教版年度会员 | 999.00 | active | 当前可用 |

历史订单编码兼容：

| 历史 product_code | 归并到当前 product_code |
|---|---|
| `prd_20260518095708_9f77db` | `subscription_trial_month` |
| `prd_20260601055439_3c4f56` | `premium_monthly_trial` |

调用时传当前编码即可命中兼容的历史编码。例如传 `subscription_trial_month`，会同时匹配 `subscription_trial_month` 和 `prd_20260518095708_9f77db`。

## 批量查询订单

```http
GET /api/external/orders
```

### Query 参数

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---:|---|---|
| `provider` | string | 否 | `all` | `all`、`wechat`、`alipay`、`wechat_shop` |
| `paid_from` | integer | 否 | - | 付款开始时间，秒级 Unix 时间戳；传入后只返回已付款订单 |
| `paid_to` | integer | 否 | - | 付款结束时间，秒级 Unix 时间戳；传入后只返回已付款订单 |
| `created_from` | integer | 否 | - | 订单创建开始时间，秒级 Unix 时间戳 |
| `created_to` | integer | 否 | - | 订单创建结束时间，秒级 Unix 时间戳 |
| `product_code` | string | 否 | - | 产品编码，见上方产品编码对照 |
| `payment_status` | string | 否 | - | `pending`、`paid`、`closed`、`failed`、`refund_processing`、`partial_refunded`、`full_refunded` |
| `is_paid` | boolean string | 否 | - | `true` 或 `false` |
| `is_refunded` | boolean string | 否 | - | `true` 或 `false`；退款中也算 `true` |
| `order_no` | string | 否 | - | 商户订单号；微信小店为小店订单号 |
| `transaction_id` | string | 否 | - | 微信/支付宝/微信小店支付单号 |
| `mobile` | string | 否 | - | 手机号 |
| `external_userid` | string | 否 | - | 企业微信外部联系人 ID |
| `unionid` | string | 否 | - | 微信 unionid |
| `limit` | integer | 否 | `100` | 每页条数，最大 `500` |
| `cursor` | string | 否 | - | 下一页游标，使用上一页返回的 `next_cursor` |

不支持按商品名称筛选；外部对接统一使用 `product_code`。

### 请求示例

拉取 2026-06-01 至 2026-06-11 的所有已付款订单：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/orders?provider=all&paid_from=1780272000&paid_to=1781222399&is_paid=true&limit=100"
```

只拉取订阅版月付：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/orders?provider=all&product_code=subscription_monthly&is_paid=true&limit=100"
```

只拉取微信小店订单：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/orders?provider=wechat_shop&is_paid=true&limit=100"
```

### 响应字段

| 字段 | 说明 |
|---|---|
| `ok` | 是否成功 |
| `items` | 订单数组 |
| `next_cursor` | 下一页游标；为空表示没有下一页 |
| `has_more` | 是否还有下一页 |
| `limit` | 当前页大小 |
| `total` | 当前查询估算总数 |

`items[]` 字段：

| 字段 | 说明 |
|---|---|
| `provider` | 渠道：`wechat`、`alipay`、`wechat_shop` |
| `order_no` | 商户订单号；微信小店为小店订单号 |
| `transaction_id` | 平台支付单号 |
| `paid_at` | 付款时间，服务端展示格式 |
| `created_at` | 创建时间，服务端展示格式 |
| `product_code` | 产品编码 |
| `payment_status` | 标准支付状态 |
| `status_label` | 状态展示文案 |
| `amount_total` | 金额，单位分 |
| `amount_yuan` | 金额，单位元，字符串 |
| `currency` | 币种 |
| `is_paid` | 是否已付款 |
| `is_refunded` | 是否退款或退款中 |
| `refund_status` | 退款状态 |
| `refunded_amount_total` | 已退款金额，单位分 |
| `mobile` | 手机号 |
| `unionid` | 微信 unionid |
| `external_userid` | 企业微信外部联系人 ID |
| `detail_url` | 详情接口路径 |

响应示例：

```json
{
  "ok": true,
  "items": [
    {
      "provider": "wechat",
      "order_no": "WXP202606110001",
      "transaction_id": "4200000000000000000",
      "paid_at": "2026-06-11 10:30:22",
      "created_at": "2026-06-11 10:29:58",
      "product_code": "subscription_monthly",
      "payment_status": "paid",
      "status_label": "已支付",
      "amount_total": 1990,
      "amount_yuan": "19.90",
      "currency": "CNY",
      "is_paid": true,
      "is_refunded": false,
      "refund_status": "",
      "refunded_amount_total": 0,
      "mobile": "138****0000",
      "unionid": "xxx",
      "external_userid": "wm_xxx",
      "detail_url": "/api/external/orders/WXP202606110001?provider=wechat"
    }
  ],
  "next_cursor": "",
  "has_more": false
}
```

## 查询订单详情

```http
GET /api/external/orders/{order_no}
```

### Query 参数

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---:|---|---|
| `provider` | string | 否 | `auto` | `auto`、`wechat`、`alipay`、`wechat_shop` |

### 请求示例

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/orders/WXP202606110001?provider=wechat"
```

微信小店详情：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/orders/3705115058471208928?provider=wechat_shop"
```

详情响应复用统一订单详情投影，包含客户、商品编码、金额、可退款金额、已退款/退款中金额、回调摘要和时间线。外部对接统一按 `product_code` 识别产品。

## 分页规则

首次请求不传 `cursor`。如果响应中：

```json
{
  "has_more": true,
  "next_cursor": "xxxx"
}
```

下一页请求原筛选条件保持不变，追加 `cursor`：

```bash
curl -H "Authorization: Bearer $CRM_API_KEY" \
"https://www.youcangogogo.com/api/external/orders?provider=all&is_paid=true&limit=100&cursor=xxxx"
```

不要自行拼 `offset`。

## 微信小店订单来源

微信小店订单查询读取本地 `wechat_shop_orders`。当前订单进入本地库的链路是：

1. 微信小店回调 `/api/wechat-shop/notify`。
2. 系统从回调中提取 `order_id`。
3. 系统使用 `WECHAT_SHOP_APPID` 和 `WECHAT_SHOP_APPSECRET` 获取 `stable_token`。
4. 系统调用微信官方 `/channels/ec/order/get` 拉取该订单详情。
5. 系统写入 `wechat_shop_orders` 和 `wechat_shop_order_events`。
6. 外部订单 API 再从本地读模型读取。

如果已知订单号但本地没有记录，可由受控后台入口按订单号补同步：

```http
POST /api/admin/wechat-shop/orders/{order_id}/sync
```

## 时间戳说明

`paid_from`、`paid_to`、`created_from`、`created_to`、`submitted_from`、`submitted_to`、`start_time` 都必须传秒级 Unix 时间戳。

示例：

| 时间 | 秒级时间戳 |
|---|---:|
| 2026-06-01 00:00:00 UTC | `1780272000` |
| 2026-06-11 23:59:59 UTC | `1781222399` |

不要传毫秒级时间戳，例如 `1780272000000` 会被拒绝。
