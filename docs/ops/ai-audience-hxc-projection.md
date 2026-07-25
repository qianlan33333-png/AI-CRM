# 黄小璨会员使用状态投影运行手册

## 边界

`ai_audience_hxc_member_usage_projection` 是按 `(generation, unionid, owner_userid)`
组织的内部读模型。它只保存规范身份、单向手机号哈希、会员/注册/真实使用状态和来源摘要，
不保存手机号、`external_userid` 或 `person_id`。

刷新器支持每日全量校准和三分钟一批的自动增量维护。增量刷新只在当前 active generation
内原子删除并重算脏 unionid，不创建新 generation；每日全量仍通过 staging generation
原子切换，负责校准删除、身份键变更和 `daily_only` 来源。在线
`audience_read.huangxiaocan_member_usage_status_v1` 仍保持原实现，必须由后续独立变更切换。
刷新过程不补业务数据、不重放事件、不调用外部系统。

## 前置条件

- 数据库 Alembic head 已包含 `0150_crm_identity_updated_cursor_index`。
- 从已发布的精确源码 SHA 运行命令。
- Web 默认语句超时保持不变；刷新事务局部使用 30 秒语句超时、3 秒锁超时并关闭 JIT。

## 命令

先读取不含个人信息的状态：

```bash
python scripts/ops/refresh_ai_audience_hxc_projection.py --status
```

经明确批准后执行一次全量校准：

```bash
python scripts/ops/refresh_ai_audience_hxc_projection.py --full
```

手动执行一个有界增量批次（通常由 timer 自动运行）：

```bash
python scripts/ops/refresh_ai_audience_hxc_projection.py --incremental --batch-size 5000
```

`batch-size` 会强制限制在 1 到 5000。游标分别使用
`crm_user_identity(updated_at, unionid)`、快照/消息/任务/激活源的 `(时间, id)`；
公开状态只输出来源、时间水位和是否有积压，不输出稳定排序键、手机号、`external_userid`
或 `unionid`。没有可靠变更时间的旧订阅表和派生注册视图标记为 `daily_only`。

生产 `aicrm-ai-audience-daily-intent.timer` 每三分钟写入一个幂等的黄小璨增量刷新意图，
内部队列运行 `ai_audience_hxc_incremental_projection_consumer`；每日 02:00 或错过窗口后的
首次补偿时写入全量刷新意图。黄小璨刷新与普通 AI Audience 包刷新使用不同事件和 consumer，
任一链路失败不会回滚或替代另一条链路的结果。退役中的 legacy scheduler 只保留同样的精确
event/consumer allowlist 作为回滚保护，不是生产刷新 owner。

命令使用事务级 advisory lock 保证单实例执行。成功时新 generation 完整写入后才原子切换；
失败时事务回滚并保留上一 ready generation，只记录稳定错误码，不记录 SQL 绑定值或异常正文。

## 成功判定

- 退出码为 0，结果中的 `ok` 为 `true`。
- `status.status` 为 `ready`，`active_generation` 大于 0，`projected_row_count` 与当次结果一致。
- 全量时 `last_full_refreshed_at` 更新；增量时 `last_incremental_watermark_at` 按所有增量源中
  最旧水位推进。
- 增量结果的 `scanned_change_count` 不超过 5000；`has_more=true` 时由下一次 tick 续扫。
- 未修改在线视图，未产生业务事件或外部调用。

## 失败与回滚

- `projection_refresh_busy`：已有刷新持有锁，不重叠执行，稍后重新读取状态。
- `projection_query_timeout` 或 `projection_lock_timeout`：停止重试并检查查询计划、锁和连接状态。
- `projection_refresh_failed`：检查服务端受控日志；状态输出不会泄露原始异常或个人信息。
- `projection_full_refresh_required`：当前没有 ready generation；增量任务安全跳过，等待每日全量
  或经批准手动执行 `--full`，不得创建空在线代次。
- 代码回滚到上一生产 SHA 即可；不要执行 schema downgrade，不要手工删除 active generation。
