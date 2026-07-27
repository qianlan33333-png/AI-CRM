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

## 换取访问 Token

客户端 secret 只通过授权的 secret store 交付，不写入 Git、普通配置、URL、日志或命令参数。以下交互式调用由 `curl` 隐藏提示输入密码：

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

轮换后通过授权的 secret-store 通道更新调用方，重启受影响的独立调用进程，并重新执行 readiness。认证状态缓存最长 30 秒；紧急吊销后应等待缓存失效或重启 runtime 再验证旧 JWT 被拒绝。

## 单 release 切换与回滚

切换顺序固定为：数据库/config 备份 → 部署 exact SHA → migration → client bootstrap → readiness → runtime restart → `/health` 与 `X-AICRM-Release-SHA` 核对 → count-only reconciliation → 最小权限 canary。

禁止旧新凭据双栈或 fallback。任何 readiness、登录、Worker scope、Webhook 防重放或 canary 失败，都停止真实外呼并整包恢复上一 verified release、对应 runtime config 和数据库备份；恢复后重新核对 release SHA、健康检查与队列 count。
