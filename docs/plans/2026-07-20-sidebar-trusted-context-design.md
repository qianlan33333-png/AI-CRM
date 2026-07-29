# 侧边栏信任上下文设计

## 目标

侧边栏授权不再读取或等待本地“外部联系人—客服”关系镜像。只要企业微信 OAuth 已确认当前员工身份，并且当前会话携带外部联系人 ID，服务端就签发绑定员工、客户、企业和会话的短期授权 Token。

## 方案选择

评估过三种方案：

1. 继续使用本地关系镜像做授权门禁：接口快，但新增跟进关系可能因同步遗漏而长期拒绝，已被本次生产问题否定。
2. 每次打开侧边栏都实时查询企业微信关系：数据新鲜，但增加首屏网络调用、延迟和企微接口失败面，不符合本次“加载简单、直接信任”的要求。
3. 信任 OAuth 员工身份与当前外部联系人 ID：不查询关系镜像，也不新增实时企微关系校验；继续保留签名 state、HttpOnly 会话 Cookie、客户绑定、企业绑定和短期 Token。采用此方案。

## 数据流

1. 前端通过现有 JSSDK 取得当前外部联系人 ID。
2. OAuth callback 通过企业微信换取当前员工 ID。
3. callback 将员工 ID、外部联系人 ID、企业 ID 和随机 session ID 写入签名 HttpOnly Cookie，不读取客户关系表。
4. `/api/sidebar/jssdk-config` 校验 Cookie 中的客户必须与当前请求客户一致，然后签发短期 `sidebar_owner_token`。
5. 现有 `/api/sidebar/*` 路由继续校验 Token、Cookie、客户、企业和 session fingerprint，防止跨客户复用旧 Token。

## 边界与错误处理

- 保留 OAuth state、code、企业配置、员工 ID 缺失、客户 ID 缺失及会话客户不一致的拒绝逻辑。
- 删除 `viewer_not_in_contact_owner_scope` 和 `owner_candidates_count`，不再调用 `ListExternalContactOwnerCandidatesQuery`。
- 不修改页面布局、组件、JSSDK 获取客户逻辑、侧边栏业务接口或全局联系人同步任务。
- 企微 OAuth 或后续发送接口返回错误时，沿用现有错误处理；本次不新增兜底关系校验。

## 验证

- 无 OAuth 会话时仍不能签发 Token。
- OAuth 员工即使不在任何本地关系镜像中，也能完成 callback 并取得 Token。
- OAuth 会话绑定的客户与请求客户不一致时仍拒绝。
- 侧边栏聚焦测试、路由/架构门禁与静态差异检查通过。
