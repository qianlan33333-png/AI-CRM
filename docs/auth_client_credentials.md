# AI-CRM 统一认证与切换手册

本文是 single-tenant 私有化部署的现行认证契约。旧共享 Bearer、URL Token、业务路由内凭据比较和 fallback 均已停用，不得继续配置或调用。

## 身份类型

- 人员：只通过企微 OAuth 登录，服务端签发不透明 Session Cookie；RBAC、`session_version`、CSRF 和敏感操作 action grant 继续生效。
- API Client / 独立 Worker：使用独立 `client_id/client_secret`，通过 TLS `POST /oauth/token` 获取默认 30 分钟、最长 60 分钟的 JWT；不签发 refresh token。
- 同进程 Worker：直接传入受控 `AuthContext` 调用 application service，不经 HTTP 给自己换 Token。
- AI-CRM 自有 Webhook：使用 raw-body HMAC；供应商 OAuth、支付和 callback 继续按供应商官方协议处理。

## 已注册机器身份

| purpose | audience | scope | 用途 |
| --- | --- | --- | --- |
| `automation_worker` | `internal_worker` | `read write` | 内部队列、事件、定时任务 |
| `archive` | `internal_worker` | `read write` | 消息归档同步 |
| `callback` | `internal_worker` | `read write` | 独立 callback worker |
| `group_broadcast` | `external_integration` | `write` | 群运营广播入口 |
| `identity` | `external_integration` | `read` | 身份解析 |
| `mcp` | `external_integration` | `read write` | MCP 集成 |
| `external_agent` | `external_integration` | `read write` | 订单、问卷、聊天与 AI 人群外部 API |
| `campaign_agent` | `external_integration` | `read write` | 仅客户/素材与 Campaign draft/status |

每个 purpose 使用独立客户端和 secret reference。`campaign_agent` 不具备审批、启动、直接发送、退款、密钥管理或 PII 导出能力。

## 后台自助配置（首选）

外部 API 与 MCP 调用方优先由超级管理员在「系统配置 → API 接入与 Token」中维护，无需登录服务器：

1. 打开 `/admin/config/api-clients`，选择“新建客户端”。
2. 选择固定类型 `External API` 或 `MCP`，填写显示名称、Client ID、15/30/60 分钟有效期和可选 CIDR 白名单。
3. 创建结果默认停用。页面只显示一次 Client Secret，复制后必须勾选确认。
4. 页面把 Secret 仅回传本次内存自检；服务端验证 Secret、签发并本地校验短期 JWT，成功后才启用。
5. 后续停用、编辑或轮换均在客户端详情页完成。轮换会立即提高 `auth_version`、停用客户端，并使旧 Secret 与旧 JWT 失效。

页面不保存或展示长期 Access Token，也不能找回已有 Client Secret。刷新或离开一次性凭据页面后，只能重新轮换。`config_admin` 可以查看掩码状态；创建、编辑、轮换与启停仅允许拥有 `manage_api_clients` 的 `super_admin`。

后台只允许以下两个权限模板，管理员不能自由拼装 scope/capability：

| 页面类型 | purpose | audience | scope | capability |
| --- | --- | --- | --- | --- |
| External API | `external_agent` | `external_integration` | `read write` | `external_read external_write` |
| MCP | `mcp` | `external_integration` | `read write` | `mcp_read mcp_execute` |

历史客户端 `aicrm-external-reader-qianlan` 会自动出现在页面中，但继续保持原 `read` / `external_read` 权限，不自动扩展写权限。部署 bootstrap 创建的系统预置客户端只读展示，仍由运行环境 Secret 引用管理；内部 Worker、Webhook HMAC、企微与支付密钥不在此页面范围内。

## 换取访问 Token

客户端 secret 只通过后台一次性凭据区或授权的 secret store 交付，不写入 Git、普通配置、URL、日志或命令参数。以下交互式调用由 `curl` 隐藏提示输入密码：

```bash
export AICRM_BASE_URL='https://www.youcangogogo.com'
export AICRM_CLIENT_ID='<registered-client-id>'

TOKEN_RESPONSE="$(
  curl --fail-with-body --silent --show-error \
    --user "$AICRM_CLIENT_ID" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode 'audience=external_integration' \
    --data-urlencode 'scope=read write' \
    "$AICRM_BASE_URL/oauth/token"
)"
export AICRM_ACCESS_TOKEN="$(jq -er '.access_token' <<<"$TOKEN_RESPONSE")"
```

调用业务 API 时只在 Header 传递短期 Token：

```bash
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $AICRM_ACCESS_TOKEN" \
  "$AICRM_BASE_URL/api/external/orders?limit=20"
```

不得将访问 Token 放入 query/path。Token 过期后重新执行 `client_credentials`，不使用 refresh token。

## Webhook HMAC

AI-CRM 自有 Webhook 必须发送：

- `X-AICRM-Client-Id`
- `X-AICRM-Timestamp`
- `X-AICRM-Event-Id`
- `X-AICRM-Signature`

签名消息为 `timestamp + "\n" + event_id + "\n" + raw_body`，算法为 HMAC-SHA256。服务端强制时间窗、CIDR（配置时）与 `event_id` 持久化防重放。签名实现以 `aicrm_next/platform/platform_foundation/auth_platform/webhook_hmac.py` 为准。

## 生产 bootstrap 与 readiness

先完成数据库备份和 `0104_auth_platform` migration，再执行 dry-run。以下命令不会打印 secret：

```bash
RUNTIME_ENV='/etc/aicrm/runtime.env'
AUTH_ISSUER='https://www.youcangogogo.com/oauth'

python scripts/ops/bootstrap_auth_clients.py \
  --database-url "$DATABASE_URL" \
  --secret-store-dir "$AICRM_SECRET_STORE_DIR" \
  --environment-file "$RUNTIME_ENV" \
  --issuer "$AUTH_ISSUER"

python scripts/ops/bootstrap_auth_clients.py \
  --database-url "$DATABASE_URL" \
  --secret-store-dir "$AICRM_SECRET_STORE_DIR" \
  --environment-file "$RUNTIME_ENV" \
  --issuer "$AUTH_ISSUER" \
  --apply

python scripts/ops/check_auth_readiness.py \
  --database-url "$DATABASE_URL" \
  --secret-store-dir "$AICRM_SECRET_STORE_DIR" \
  --environment-file "$RUNTIME_ENV" \
  --issuer "$AUTH_ISSUER"
```

readiness 必须返回 `ok=true`、`failure_count=0`、`secrets_printed=false`，才允许重启新 release。

## 停用、启用与轮换

后台创建的 External API / MCP 客户端优先在 `/admin/config/api-clients/{client_id}` 操作。活动客户端需先停用才能修改名称、Token 有效期或 CIDR；不支持删除，以便保留完整审计。系统预置客户端继续使用以下运维命令：

状态检查和紧急吊销不会输出 secret：

```bash
python scripts/ops/manage_auth_clients.py --database-url "$DATABASE_URL" status
python scripts/ops/manage_auth_clients.py --database-url "$DATABASE_URL" disable --purpose external_agent
python scripts/ops/manage_auth_clients.py --database-url "$DATABASE_URL" enable --purpose external_agent
```

轮换会提高 `auth_version`、使旧 JWT 失效，并把新 secret reference 原子写回权限为 `0600` 的 runtime env 文件：

```bash
python scripts/ops/manage_auth_clients.py \
  --database-url "$DATABASE_URL" \
  --secret-store-dir "$AICRM_SECRET_STORE_DIR" \
  --environment-file /etc/aicrm/runtime.env \
  rotate --purpose external_agent
```

轮换后通过授权的 secret-store 通道更新调用方，重启受影响的独立调用进程，并重新执行 readiness。外部集成 JWT 每次校验都以数据库中的当前 `enabled/auth_version` 为权威，后台停用或轮换后无需重启即可立即拒绝旧 JWT；内部 Worker 仍可使用短时状态缓存。

## 单 release 切换与回滚

切换顺序固定为：数据库/config 备份 → 部署 exact SHA → migration → client bootstrap → readiness → runtime restart → `/health` 与 `X-AICRM-Release-SHA` 核对 → count-only reconciliation → 最小权限 canary。

禁止旧新凭据双栈或 fallback。任何 readiness、登录、Worker scope、Webhook 防重放或 canary 失败，都停止真实外呼并整包恢复上一 verified release、对应 runtime config 和数据库备份；恢复后重新核对 release SHA、健康检查与队列 count。
