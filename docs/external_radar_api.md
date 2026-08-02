# 内容雷达外部只读 API

## 能力边界

该接口供外部系统只读拉取“用户点击过哪个雷达、何时点击”以及雷达编号与标题的当前映射。

- 生产 Base URL：`https://www.youcangogogo.com`
- 只读本地 AI-CRM Next 读模型，不创建或修改雷达，不触发企微、Webhook、OAuth 或其他外部调用。
- 点击接口只输出逻辑点击，不暴露 OAuth、跳转、图片/PDF 加载等内部阶段。
- 不输出 openid、external_userid、IP、User-Agent 或 Referer。

## 鉴权

复用系统唯一的 CRM 开放 API Key。配置管理员或超级管理员在「系统配置 → CRM 开放 API Key」（`/admin/config/api-key`）生成后，调用方只需要保存这一个值：

```bash
export CRM_API_KEY='<后台仅展示一次的唯一 API Key>'
```

该 Key 固定只有 `read` scope 与 `external_read` capability。完整的生成、重新生成、停用和失效规则见 [auth_client_credentials.md](auth_client_credentials.md)。

业务请求统一携带：

```http
Authorization: Bearer <CRM_API_KEY>
```

## 查询逻辑点击

```http
GET https://www.youcangogogo.com/api/external/radar-clicks
```

### Query 参数

所有业务过滤条件均可选；不传时按最新点击开始分页拉取。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `mobile` | string | - | 精确匹配统一身份中的手机号 |
| `unionid` | string | - | 精确匹配微信 unionid |
| `radar_id` | integer | - | 雷达数字 ID，必须大于 0 |
| `radar_code` | string | - | 雷达代码 |
| `clicked_from` | integer | - | 点击开始时间，秒级 Unix 时间戳，包含边界 |
| `clicked_to` | integer | - | 点击结束时间，秒级 Unix 时间戳，包含边界 |
| `limit` | integer | `100` | 每页 1–500 条 |
| `cursor` | string | - | 上一页返回的不透明 `next_cursor`，不要自行构造 |

多个过滤条件同时出现时按 AND 关系处理。毫秒时间戳、倒置的时间范围或非法 cursor 返回 `400 invalid_request`。

### 请求示例

```bash
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $CRM_API_KEY" \
  'https://www.youcangogogo.com/api/external/radar-clicks?mobile=13800138000&clicked_from=1784044800&limit=100'
```

### 响应示例

```json
{
  "ok": true,
  "items": [
    {
      "event_id": 9812,
      "mobile": "13800138000",
      "unionid": "oExampleUnionId",
      "radar_id": 32,
      "radar_code": "Ab12Cd34",
      "clicked_at": "2026-07-15T08:30:12+00:00",
      "identity_status": "complete",
      "identity_matched_by": "unionid"
    }
  ],
  "total": 1,
  "limit": 100,
  "next_cursor": "",
  "has_more": false,
  "filters": {
    "mobile": "13800138000",
    "clicked_from": 1784044800
  },
  "route_owner": "ai_crm_next",
  "source_status": "external_radar_clicks",
  "fallback_used": false
}
```

`identity_status` 说明：

| 值 | 说明 |
|---|---|
| `complete` | 手机号和 unionid 均已从唯一有效身份补齐 |
| `mobile_missing` | 有可信 unionid，但手机号当前为空 |
| `unresolved` | 事件来自可信身份会话，但暂未归一到手机号和 unionid |
| `conflict` | 身份别名对应多个候选；接口不选择任意候选，手机号和 unionid 留空 |

首次需授权的打开以 `authorized` 计一次；签名身份会话内再次打开以携带可信身份的 `landing` 计一次。匿名 landing、OAuth 中间阶段、redirect 和内容加载事件不会出现在该接口中。

## 查询雷达编号与标题

```http
GET https://www.youcangogogo.com/api/external/radar-links
```

### Query 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `radar_id` | integer | - | 精确匹配雷达数字 ID |
| `radar_code` | string | - | 精确匹配雷达代码 |
| `limit` | integer | `100` | 每页 1–500 条 |
| `cursor` | string | - | 上一页返回的不透明 `next_cursor` |

接口返回所有未软删除的雷达，包括当前已停用的雷达，以便解释历史点击。

```bash
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $CRM_API_KEY" \
  'https://www.youcangogogo.com/api/external/radar-links?limit=100'
```

```json
{
  "ok": true,
  "items": [
    {
      "radar_id": 32,
      "radar_code": "Ab12Cd34",
      "title": "7 月直播报名资料"
    }
  ],
  "total": 1,
  "limit": 100,
  "next_cursor": "",
  "has_more": false,
  "filters": {},
  "route_owner": "ai_crm_next",
  "source_status": "external_radar_links",
  "fallback_used": false
}
```

## 公共错误语义

| 场景 | HTTP | 错误字段 |
|---|---:|---|
| 未携带 Token | `401` | `error=access_token_required` |
| Token 无效或签名错误 | `401` | `error=invalid_access_token` |
| Key 已停用 | `401` | `error=client_disabled` |
| audience、scope 或 capability 不满足 | `403` | `error=invalid_target` 或 `scope_or_capability_required` |
| 参数、时间戳或 cursor 非法 | `400` | `error_code=invalid_request` |
| 生产读模型不可用 | `503` | `error_code=production_unavailable` |

无匹配数据不是错误，返回 `200`、`items=[]`、`total=0`。
