# 后台登录与 RBAC

## 认证与授权边界

- 人员唯一上游身份是企业微信 OAuth/扫码登录。
- AI-CRM 使用本地 `admin_users`、角色和 capability 决定“能做什么”。
- 本地用户名/密码登录和 break-glass 密码入口已移除，`POST /login` 不会签发 Session。
- 机器调用使用独立 client credentials；人员 Session 与机器 JWT 不混用。完整机器认证见 [`auth_client_credentials.md`](auth_client_credentials.md)。

企微回调成功后，系统使用 `UserId + CorpId` 匹配已启用的 `admin_users`，读取角色和 `session_version`，再签发服务端 Session。未授权或已停用成员不能进入后台。

## 登录入口

- 登录页：`GET /login`
- 企微登录启动：`GET /auth/wecom/start`
- 企微登录回调：`GET /auth/wecom/callback`
- 登出：`GET /logout`

## 角色

- `super_admin`：全部后台能力和授权成员管理；独占 `manage_api_clients`，可创建、轮换和启停外部 API/MCP 客户端。
- `automation_admin`：自动化与运营相关读写能力。
- `questionnaire_admin`：问卷相关读写能力。
- `config_admin`：系统配置相关读写能力；可生成、轮换和停用系统唯一的 CRM 开放 API Key；可查看其他 API 客户端状态，但不能创建、编辑、轮换或启停 OAuth/MCP 客户端。
- `viewer`：只读；所有写操作返回 `403`。

最终授权以 route policy 声明的 capability 和 `aicrm_next/platform/admin_auth/capabilities.py` 映射为准。

## Session 与 CSRF

- Session Cookie：`aicrm_next_admin_session`，只保存随机凭据；数据库只保存 HMAC digest。
- CSRF Cookie：`aicrm_next_csrf`，人员写请求同时提交 `X-CSRF-Token` 或同源表单字段。
- 默认有效期：8 小时。
- Cookie：`SameSite=Lax`；Session 为 `HttpOnly`；生产环境强制 `Secure`。
- 登录成功会签发新 Session；登出、成员停用、角色/权限变化或 `session_version` 增加都会使旧 Session 失效。
- 高风险人员写操作在 Session + CSRF 之外，还需要绑定 session、capability、method、route 和 action 的短期 action grant。

## 授权管理

入口：`/admin/config/detail/admin_access`

支持查看授权成员、创建成员、编辑角色、启停成员以及查看最近登录审计。修改授权后必须验证旧 Session 被拒绝，并由成员重新企微登录。

## 机器调用边界

内部 Worker、CLI 和外部 Agent 不使用后台 Cookie。内部 Worker、MCP 与写操作仍按 workload 使用注册 client 换取短期 JWT；CRM 开放只读接口另支持后台生成、数据库哈希存储的单例 API Key，不接受运行环境共享 Bearer。业务 Handler 不读取 Cookie、Authorization 或 secret，只消费统一 middleware 注入的 `AuthContext`。
