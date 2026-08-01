# AI Audience 人群包

AI Audience 是 AI-CRM Next 内的模板化运行时人群包能力，用于把运营自然语言需求转换成可刷新、可预览、可绑定自动化话术、可群发复用的标准目标集合。新建业务包由固定模板编译，Agent 和运营都只需要阅读 [`agent_package_configuration_guide.md`](agent_package_configuration_guide.md)，不读取 schema、不生成 SQL。它不恢复旧 automation program / Runtime V2，也不新增私信群发发送器。

## 产品边界

业务人群包不得通过 PR 新增。新增一个普通运营包，例如“某负责人企微未注册人群”，必须走运行时 External API 写入 DB 配置；如果新增一个具体包还需要迁移、代码或几百行 PR，说明流程错误。

PR 只用于平台能力变更，包括：

- 新增或修正底层 `audience_read.*` 数据源 view。
- 新增 AI Audience 平台 API、刷新、内部自动化传递、安全或审计能力。
- 修改 SQL 安全边界、鉴权、prefix gate、运行时存储结构。

普通业务包上线不需要提交 repo 文件。Agent 从不可变模板注册表选择 `template_key` 并填写业务字段，服务端负责引用解析、参数化编译、校验和预览。`docs/ai_audience/examples/` 里的旧文件只用于兼容说明，不代表每个业务包都要新增一个 `.md`。

## 运行时创建流程

统一使用 Template Package：

1. Agent 阅读唯一说明书并选择六类固定模板之一。
2. 调 `POST /api/external/ai-audience/templates/preview` 做只读预览。
3. 用户没有明确要求“只预览”时，Preview 成功且人数非零后调 `POST /api/external/ai-audience/templates/apply`。
4. Apply 原子创建 package/version/senders/group/binding，结果固定为 `paused`。
5. 运营在详情页复核、修正、绑定并人工启用。

旧 Simple SQL 和 Markdown spec 仅保留底层兼容，不用于新 Agent 配置。兼容路由包括：

- `POST /api/external/ai-audience/spec/dry-run`
- `POST /api/external/ai-audience/spec/apply`
- `POST /api/external/ai-audience/spec/publish`
- `POST /api/external/ai-audience/packages/{package_key}/archive`

两条链路都使用注册 `external_agent` 换取的短期 JWT（`external_integration` audience、`write` scope、`external_write` capability；见 [`../auth_client_credentials.md`](../auth_client_credentials.md)），并由服务端强制执行 `AICRM_AI_AUDIENCE_SPEC_ALLOWED_PREFIXES`、`AICRM_AI_AUDIENCE_SPEC_ALLOW_NON_VERIFY_PREFIX` 和 `AICRM_AI_AUDIENCE_SPEC_ALLOW_PUBLISH`。

## Simple SQL Package（底层兼容）

以下内容只用于维护存量包，不是 Agent 新建人群包的操作指南。新建配置不得回退到 SQL。

Simple SQL 只需要返回 `external_userid`：

```json
{
  "package_key": "audience_hyc_wecom_unregistered",
  "name": "HuangYouCan 企微未注册人群",
  "natural_language_definition": "负责人为 HuangYouCan，已经添加企业微信，但还没有完成注册的用户。",
  "refresh_mode": "every_3m",
  "sql": "SELECT DISTINCT wc.external_userid FROM audience_read.wecom_contacts_v1 wc LEFT JOIN audience_read.registration_status_v1 r ON r.external_userid = wc.external_userid WHERE wc.owner_userid = :owner_userid AND COALESCE(r.is_registered, false) = false",
  "parameters": {
    "owner_userid": "HuangYouCan"
  },
  "senders": [
    {
      "sender_userid": "HuangYouCan",
      "priority": 1,
      "status": "active"
    }
  ]
}
```

Simple SQL 规则：

- 只允许查询 `audience_read.*` catalog 视图。
- 禁止 `SELECT *`、DML/DDL、`public.*`、`pg_sleep` 等危险函数。
- SQL 用到的业务参数必须在 `parameters` 声明。
- 系统参数由平台自动注入：`package_key`、`package_id`、`refresh_started_at`、`last_watermark_at`、`lookback_seconds`。
- 平台会把 simple SQL 编译成 AI Audience 标准 SQL，并继续复用现有 package/version/refresh/member/internal-effect 表。
- Simple SQL Apply 不接受 `outbound_webhook_url`；自动化话术绑定通过管理端或高级 spec 的 `automation_binding.agent_code` 完成。

Simple refresh mode 只允许：

- `every_3m`
- `daily_0200`
- `manual`

## 权限边界

- `/api/admin/ai-audience/*` 只接受 admin session。
- `/api/ai/audience/*` 只接受 internal token，不能给浏览器调用。
- `/api/external/ai-audience/*` 只接受 External API token，不能绕过服务端 prefix gate。
- API 不返回 SQL、inbound secret、outbound signing secret、payload 明细或成员隐私字段。
- 企微群成员包使用 `audience_read.group_chat_members_v1`，只读取当前 `group_chats.raw_payload` 投影；群详情数据源更新机制不在 AI Audience 包内新增。

## 群发边界

一键群发只复用 User Ops 标准 batch-send：

- `POST /api/admin/user-ops/batch-send/preview`
- `POST /api/admin/user-ops/batch-send/execute`

AI Audience 只通过 `target_source=ai_audience_package` 提供标准 target rows。发送人由 package sender whitelist 解析，禁止默认兜底。
