# 黄小璨会员使用状态投影运行手册

## 边界

`ai_audience_hxc_member_usage_projection` 是按 `(generation, unionid, owner_userid)`
组织的内部读模型。它只保存规范身份、单向手机号哈希、会员/注册/真实使用状态和来源摘要，
不保存手机号、`external_userid` 或 `person_id`。

当前刷新命令只负责全量校准并原子切换 active generation。在线
`audience_read.huangxiaocan_member_usage_status_v1` 仍保持原实现，必须由后续独立变更切换。
刷新过程不补业务数据、不重放事件、不调用外部系统。

## 前置条件

- 数据库 Alembic head 已包含 `0149_ai_audience_hxc_projection`。
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

命令使用事务级 advisory lock 保证单实例执行。成功时新 generation 完整写入后才原子切换；
失败时事务回滚并保留上一 ready generation，只记录稳定错误码，不记录 SQL 绑定值或异常正文。

## 成功判定

- 退出码为 0，结果中的 `ok` 为 `true`。
- `status.status` 为 `ready`，`active_generation` 大于 0，`projected_row_count` 与当次结果一致。
- `last_full_refreshed_at` 与 `last_refresh_finished_at` 已更新。
- 未修改在线视图，未产生业务事件或外部调用。

## 失败与回滚

- `projection_refresh_busy`：已有刷新持有锁，不重叠执行，稍后重新读取状态。
- `projection_query_timeout` 或 `projection_lock_timeout`：停止重试并检查查询计划、锁和连接状态。
- `projection_refresh_failed`：检查服务端受控日志；状态输出不会泄露原始异常或个人信息。
- 代码回滚到上一生产 SHA 即可；不要执行 schema downgrade，不要手工删除 active generation。
