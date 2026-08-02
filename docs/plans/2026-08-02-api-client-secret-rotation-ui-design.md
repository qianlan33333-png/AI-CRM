# API Client Secret 轮换确认页设计

## 目标

让超级管理员在“API 接入与 Token”的客户端详情页明确看见当前正在生效的 Client Secret 标识，并能完成“确认轮换、一次性复制、自检启用、关闭后核对”的完整流程。交互直接复用已验证的 CRM API Key 页面模式。

## 方案选择

采用二级详情页管理方案。一级页继续只承担统计、搜索、筛选和入口，但把非预置客户端的入口文案改为“管理 Secret”，提高能力可发现性。详情页顶部新增“当前 Client Secret”状态卡，展示脱敏标识、启停状态、认证版本和最近轮换时间。

未采用列表内直接轮换，因为轮换会使旧 Secret 和已有 JWT 立即失效，属于需要上下文说明和确认的高风险动作。未采用多 Secret 列表，因为当前认证协议只有一个有效 Secret，新增多版本并存会改变安全模型。

## 交互流程

1. 点击“轮换 Secret”后打开确认弹窗，说明客户端会立即停用，旧 Secret 与 JWT 同时失效。
2. 确认后服务端生成新 Secret、提升 `auth_version`、写入安全脱敏标识并停用客户端。
3. 弹窗切换为一次性凭据视图；完整 Secret 仅保存在当前页面内存中。
4. 管理员复制并确认已保存后，点击“自检并启用”。服务端验证 Secret、签发并本地校验短期 JWT，成功后启用客户端。
5. 关闭弹窗会清空完整 Secret。页面继续显示脱敏标识、版本、轮换时间和当前启停状态。
6. 若管理员关闭前未启用，客户端保持停用；之后可使用已保存的 Secret 在详情页完成自检启用。

## 架构与安全边界

- capability owner：`platform.admin_config` / `auth_platform`。
- 路由：现有 Next-owned `/admin/config/api-clients/{client_id}`、`POST .../rotate-secret`、`POST .../activate`、`PUT .../enabled`。
- 不新增认证协议，不改变 `/oauth/token` 契约。
- 不涉及真实外部调用；自检仅在本地认证服务内签发和校验 JWT。
- 不写入 fixture 或演示数据，不修改 systemd、nginx 或运行环境 Secret。
- 系统预置客户端继续只读；历史只读客户端保留原 scopes/capabilities。
- Secret、哈希、JWT 和 Secret Store reference 不进入列表、日志、审计或普通详情响应。
- 回滚使用上一 release；`credential_hint` 是已有增量列，保留不回退。

## 验证

- 后端：创建与轮换写入脱敏标识；旧 JWT 失效；自检启用；预置客户端拒绝轮换；历史权限不扩张。
- 前端：列表入口可发现；确认弹窗；一次性 Secret；关闭清空；复制确认后启用；无 local/session storage；无重复页面标题。
- 视觉：桌面与窄屏检查状态卡、弹窗、表单和操作区，不出现横向溢出。
