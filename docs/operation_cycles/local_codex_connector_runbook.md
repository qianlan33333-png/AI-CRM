# 运营闭环本地 Codex 连接器运行手册

本连接器只负责从 CRM 出站领取动作、在本机 Codex app-server 创建可对话 task，并把最终脱敏结论回传 CRM。它不保存或上传本地 Excel，不批准 AI 助手计划，也不产生真实发送。

## 上线门禁

以下两个特性开关默认均为 `false`，必须同时开启才允许发起和领取：

- `AICRM_OPERATION_ACTIONS_V1_ENABLED`
- `AICRM_LOCAL_CODEX_CONNECTOR_V1_ENABLED`

连接器只接受固定版本：`AICRM_CODEX_EXPECTED_VERSION` 必须与本机 `codex --version` 的完整输出一致；Unix socket 初始化失败或版本不一致时，心跳标记为 `unavailable/incompatible`，CRM 阻止启动。没有后台 CLI 降级路径。

## 本机配置

使用独立 `operation_runner` OAuth client 和 secret reference，不得复用 `campaign_agent` 或 `ops_reporter`：

```bash
export AICRM_BASE_URL='https://crm.example.test'
export AICRM_OPERATION_RUNNER_ID='designated-mac-1'
export AICRM_AUTH_OPERATION_RUNNER_CLIENT_ID='<registered-client-id>'
export AICRM_AUTH_OPERATION_RUNNER_CLIENT_SECRET_REF='secretref:file:OPERATION_RUNNER:v1_placeholder'
export AICRM_CODEX_BINARY='/Applications/ChatGPT.app/Contents/Resources/codex'
export AICRM_CODEX_EXPECTED_VERSION='<exact codex --version output>'
export AICRM_CODEX_APP_SERVER_SOCKET='/absolute/local/socket/codex-app-server.sock'
export AICRM_OPERATION_RUNNER_BINDINGS_FILE='/absolute/local/config/operation-bindings.json'
```

先启动固定版本的受管 app-server，并从版本回执读取 `socketPath` 写入
`AICRM_CODEX_APP_SERVER_SOCKET`：

```bash
codex app-server daemon enable-remote-control
codex app-server daemon start
codex app-server daemon version
```

连接器使用固定版本 CLI 提供的 `app-server proxy --sock <socketPath>` 完成
control socket 握手，再发送 `initialize`、`thread/start` 和 `turn/start`。该 proxy
只是本机 Unix socket 协议传输，不是后台任务执行器；禁止替换成 `codex exec`。
不要配置 `ws://` 监听或公网端口，CRM 始终只能接收本机连接器的出站请求。

绑定文件只存在本机。CRM 心跳只接收键名：

```json
{
  "hxc_knowledge_vault": "/absolute/local/knowledge-vault",
  "huangyoucan_data": "/absolute/local/huangyoucan-client",
  "excel_workspace": "/absolute/local/excel-workspace"
}
```

文件权限建议为 `0600`。值可以是本机目录或安全客户端入口，但不得是明文 access token、client secret 或 API key。

## 启动与状态

在包含 AI-CRM 源码和依赖的本机环境运行：

```bash
python3 tools/run_operation_cycle_codex_connector.py
```

运行契约：

- 15 秒一次心跳，45 秒无心跳即离线。
- 单执行器、并发度固定为 1。
- claim 最长等待 25 秒；租约 60 秒，连接器持续续租。
- `thread_id` 已存在时恢复原 task；`turn_id` 已存在时不重复发送首轮提示。
- task 完成时通过本机控制 socket 调用 `tools/submit_operation_cycle_action_result.py`，控制消息中的本地 result 文件路径不会发送 CRM。
- 最终结果 schema 会拒绝个人标识、手机号、邮箱、企微外部联系人 ID、本地路径、Excel 内容、凭据和原始对话。

## 生产启用前无发送验收

1. 只开启测试策略或受控策略的正式 `operation_cycle_skill.v1`。
2. 验证在线心跳仅包含绑定逻辑键，不含路径值。
3. 在 CRM 点击当前动作，确认侧边栏只出现一个持久 task，并收到本机通知。
4. 重启连接器，确认继续原 `thread_id`，没有第二个 task。
5. 人工确认前核对 CRM 中不存在新 preparation、AI 助手计划和 `broadcast_jobs`。
6. 使用无真实发送的准备样本完成 commit，核对 `pending_review/draft`、Excel SHA-256 一致和 `broadcast_jobs=0`。
7. 不点击 AI 助手“确认并发送”；本轮 canary 到此结束。

## 回滚

先关闭 `AICRM_OPERATION_ACTIONS_V1_ENABLED` 和 `AICRM_LOCAL_CODEX_CONNECTOR_V1_ENABLED`，再停止本地连接器。新增 Skill、动作请求、事件和 runner 心跳表保留为审计数据；原运营快照、策略上下文、报告接口、Campaign preparation 与 AI 助手发送链路不回滚、不切换到 legacy。
