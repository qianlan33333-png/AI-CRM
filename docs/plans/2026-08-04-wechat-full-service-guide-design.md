# 微信完整服务模式引导页设计

## 目标

当支付 OAuth 回调因微信“部分服务”模式隔离 Cookie 而缺少 state Cookie 时，不再把 `oauth_state_cookie_missing` JSON 暴露给用户，改为返回醒目的移动端全屏引导页，明确提示用户点击微信浏览器底部的“使用完整服务”。

## 方案

- capability owner：`public_product`。
- 影响 route：`GET /api/h5/wechat-pay/oauth/callback`，当前由 AI-CRM Next 原生实现持有。
- 仅替换“state Cookie 缺失”这一分支的呈现；非法 state、篡改 Cookie、state 不匹配等安全错误继续保留原有拒绝逻辑。
- 复用 `aicrm_next.platform.shared.wechat_identity_page` 的共享页面边界，新增无脚本、无可点击假按钮的专用 HTML 响应。
- 页面只包含一个主标题、原因说明和一条操作指令，使用大字号、高对比度提示与向下箭头指向微信原生入口。
- 响应保持 HTTP 400，并增加 `no-store`、CSP、`nosniff` 等静态错误页安全头。

## 安全与验证

- 不触发支付、OAuth token exchange、订单创建或任何其他真实外部调用。
- 不使用生产数据，不引入 fixture/local contract 展示。
- 单元测试验证 HTML 内容类型、关键文案、不泄露内部错误码、无假按钮、无外部调用，并保留一次性 state 的原有行为。
- rollback：恢复回调缺少 Cookie 分支的上一版本响应；无需数据库、配置或部署层变更。
