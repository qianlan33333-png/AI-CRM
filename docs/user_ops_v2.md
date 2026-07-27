# User Ops V2

这份文档只描述当前已经落地的 user ops 页面口径，不追溯历史讨论稿。

## 当前页面目标

- 页面地址：`/admin/user-ops/ui`
- 兼容入口：`/admin/user-ops`
- 当前页面定位：转化链路运营页
- 这不是全量后台工具页，也不是历史运营面板入口集合

## 当前保留能力

- 转化池总览
- 顶部 8 张卡片筛选
- 班期 / 关键词 / 手机号筛选
- 客户详情 drawer
- 客户 timeline drawer
- 列表勾选
- 全选当前筛选结果
- 手动选中部分
- 官方私信批量群发
- 免打扰
- 发送记录
- 导出当前筛选结果

## 当前已移除的前端入口

当前页面中不再暴露以下入口或按钮：

- 总览 tab
- 操作历史 tab
- 运营名单历史
- 待处理作业
- 班期状态
- 班期状态历史
- 导入入口
- 班期回填
- 执行待处理自动归班任务
- `reload`

说明：

- 这些能力对应的部分后端接口仍可能保留
- 但当前页面前端不再展示对应按钮、tab 或弹窗

## 当前基础池口径

- 页面基础范围固定为 `user_ops_pool_current`
- 当前页不是全量客户页
- 不在 `user_ops_pool_current` 里的记录，不应该被当前页筛出来

## 当前共享查询口径

顶部总览、列表、导出、批量发送预览、批量发送执行，共用同一套基础查询逻辑。

统一支持的筛选维度：

- 顶部卡片维度：
  - `wecom_status=added|not_added|all`
  - `mobile_binding_status=bound|unbound|all`
  - `activation_bucket=activated|not_activated|pending_input|all`
- 底部筛选：
  - `class_term_no`
  - `keyword`
  - `mobile`

规则：

- 顶部卡片筛选与底部筛选是 AND 关系
- “引流品总数”代表清空顶部维度后的总量视角

## 顶部 8 张卡片

固定为：

- 引流品总数
- 已加微
- 未加微
- 已绑手机号
- 未绑手机号
- 黄小璨已激活
- 黄小璨未激活
- 激活待录入

字段语义拆分：

- `is_added_wecom`
  - 按 `external_userid` 是否可用理解
- `is_mobile_bound`
  - 按手机号绑定关系理解
- `activation_bucket`
  - `activated`
  - `not_activated`
  - `pending_input`

## 当前列表字段口径

列表面向新页面至少返回：

- `is_added_wecom`
- `is_mobile_bound`
- `activation_bucket`
- `do_not_disturb`
- `do_not_disturb_reasons`
- `can_open_customer_detail`
- `can_batch_send`

说明：

- `can_open_customer_detail` 依赖 `external_userid`
- `can_batch_send` 依赖 `external_userid`
- 缺失 `external_userid` 的记录，默认不可发

## 客户详情与 timeline 口径

当前页面只复用：

- `GET /api/customers/<external_userid>`
- `GET /api/customers/<external_userid>/timeline`

严格限制：

- `external_userid` 是唯一详情主键
- 缺失 `external_userid`：
  - 不允许查看详情
  - 不允许手机号 fallback
  - 不允许 lead-pool fallback
  - 不允许伪造空 timeline 充当兼容

## 免打扰口径

当前页支持免打扰列和原因展示。

至少支持两类原因：

- 自动原因
  - 当前已落地规则：`已报名正价课`
- 手动原因
  - 通过 `POST /api/admin/user-ops/do-not-disturb` 设置或取消

规则：

- 自动原因和手动原因会同时展示
- 取消手动原因时，不影响自动原因
- 手动免打扰公开接口按身份键处理，不按 member_id 处理
- 当前支持用 `external_userid` 或 `mobile` 标识目标

## 批量群发口径

当前页面只做文本群发。

选择模式支持：

- 全选当前筛选结果
- 手动选中部分

预览规则：

- 默认排除免打扰用户
- 支持 `include_do_not_disturb=true`
- 开启包含免打扰时，前端要求二次确认
- 缺失 `external_userid` 的记录不会进入真正可发送名单

执行规则：

- 必须 `confirm=true`
- 真正发送时复用现有官方群发能力
- 不新造假的发送链路

## 发送记录口径

发送记录是页面级记录，不是旧“操作历史”换皮。

当前页面通过：

- `GET /api/admin/user-ops/send-records`

查看本页批量群发记录。

页面级记录表：

- `user_ops_send_records`

底层真实发送任务表仍然是：

- `outbound_tasks`

## 当前涉及的核心表

主表：

- `user_ops_pool_current`
- `user_ops_do_not_disturb`
- `user_ops_send_records`

辅助来源：

- `contacts`
- `external_contact_bindings`
- `people`
- `class_user_status_current`
- `user_ops_activation_status_source`
- `owner_role_map`
- `outbound_tasks`

## 如果模型要继续阅读当前实现

D4 legacy retirement note: the old User Ops route-owner file is no longer a
current implementation path. AI-CRM Next owns the User Ops readonly surface;
historical DND, batch-send, deferred-job, and write/external fallback evidence
is retained in the archived route inventory.

建议先读：

1. [`aicrm_next/app/admin_console/templates/admin_user_ops.html`](aicrm_next/app/admin_console/templates/admin_user_ops.html)
2. [`aicrm_next/ops_enrollment`](../aicrm_next/ops_enrollment)
3. [`docs/archive/route_inventory/user_ops_route_inventory.md`](archive/route_inventory/user_ops_route_inventory.md)
4. [`tests/test_user_ops_api.py`](../tests/test_user_ops_api.py)
