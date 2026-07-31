# AI-CRM 全局报错飞书通知

## 能力边界

该能力属于 `aicrm_next.platform.platform_foundation.error_reporting`，只做错误报告，
不写数据库、不创建业务任务、不重试业务动作，也不改变原请求或 worker 的成功/失败结果。

覆盖范围：

- FastAPI 未处理异常；
- `ApplicationError` 业务异常；
- `RepositoryProviderError` 数据源/仓储异常；
- HTTP 5xx；
- AI-CRM Python 进程的 ERROR 日志和未捕获异常；
- 通过 `scripts.script_runtime.print_json` 输出的失败 worker 结果。

为了避免告警风暴，同一事件指纹 5 分钟内只发送一次；每个进程每分钟最多发送
20 条。通知只包含脱敏后的错误摘要、组件、路由模板、状态码、错误码、异常类型、
发布 SHA 和事件指纹，不包含请求正文、查询参数、Cookie、Token、手机号或企微用户标识。

## 启用

默认关闭。生产启用必须同时满足：

1. 使用 `FileSecretStore` 保存飞书群机器人 webhook，密钥名必须是
   `AICRM_ERROR_REPORTING_FEISHU_WEBHOOK_SECRET`；
2. 将生成的引用配置为进程启动环境变量
   `AICRM_ERROR_REPORTING_FEISHU_WEBHOOK_SECRET_REF`；
3. 显式配置 `AICRM_ERROR_REPORTING_EXECUTE=1`；
4. 重启 Web、callback、worker 和 scheduler 进程，使启动配置一致生效；
5. 通过一个受控测试异常确认飞书收到 `【AI-CRM 全局报错】`，并核对发布 SHA。

Webhook 仅允许 `https://open.feishu.cn/open-apis/bot/v2/hook/...` 或对应的
Lark 官方域名，禁止重定向和内网/IP 目标。

## 失败隔离与回滚

飞书 DNS、超时、限流或返回失败时，报告器最多占用 2 秒并吞掉自身异常，原业务结果
保持不变。紧急停止通知只需将 `AICRM_ERROR_REPORTING_EXECUTE` 设为 `0` 后重启相关
进程；代码回滚使用上一发布版本，不存在 legacy 或 `production_compat` fallback。
