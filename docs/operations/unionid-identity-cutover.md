# Unionid identity cutover runbook

## Scope

本批次只收敛已有身份解析、canonical 写入和缺失身份消费语义，不新增页面、菜单、route、业务表、Customer 360 或客户生命周期能力。

## Runtime inventory

唯一 resolver owner：`aicrm_next.crm.identity_contact.resolver`。

| Consumer class | Adapter |
| --- | --- |
| identity API / questionnaire / archive / customer read model | `IdentityResolver` structured result |
| channel entry / cloud / group ops / send target / payment / service period | DBAPI adapter |
| AI Audience / automation batch / tag projection | SQLAlchemy adapter |
| local SQLite tests | explicit fixture adapter + shared all-candidate classifier |

私有 `_resolve_unionid*` runtime 数量必须为 0，由 `scripts/ci/check_unionid_identity_contract.py` 强制。

Canonical write owners：

- `aicrm_next.crm.identity_contact`：mobile bind 和 conflict/queue owner。
- `aicrm_next.channels.channel_entry`：受信任企微 detail ingress；写前锁定所有 alias 并验证归属。
- `aicrm_next.crm.sidebar_write`：已有 sidebar mobile/profile command，先 resolver、后 unionid lock。
- `aicrm_next.extensions.commerce.public_product`：已有支付手机号投影，先验证 order identity 和 mobile alias。

`people` 和 `external_contact_bindings` 不再有 identity canonical 新写。owner migration 对 binding 表的 owner metadata 更新属于历史投影维护，不建立或改写 person binding。

问卷提交继续保存表单业务事实；若 canonical 尚未解析，submission 保持空 unionid、alias 原值只进入 resolution queue，标签、webhook 和 automation consumer 保持 blocked/failed_retryable，不能以 `succeeded` 结束。

## Preflight

在 runtime stop 前执行：

```bash
python3 scripts/ops/check_unionid_identity_cutover.py \
  --phase preflight \
  --register-existing-conflicts \
  --expected-release-sha "$RELEASE_SHA"
```

`--register-existing-conflicts` 只把重复 alias 登记到现有 `crm_user_identity_conflicts`，不修改、合并、停用或选择任何 canonical identity。该操作使用事务级 advisory lock，并按 alias + open conflict 幂等；登记内容留在数据库内供后续受控人工处理，stdout 仍只有计数。

输出字段只包含计数：canonical/active/non-active、duplicate alias groups、已明确 blocked / 未登记 duplicate groups、open conflicts、pending/failed queue、missing-unionid succeeded consumers、legacy table row counts 和 resolver parity mismatch。

以下任一项非 0 时 fail closed，旧 runtime 保持运行：

- `unregistered_duplicate_alias_group_count`
- `missing_unionid_succeeded_consumer_count`
- `resolver_parity_mismatch_count`

`duplicate_alias_group_count` 可以非 0，但每一组都必须计入 `blocked_duplicate_alias_group_count`、存在 open conflict，且新 resolver 对该 alias fail closed。历史 conflict、pending 和 failed queue 仅计数并继续由人工/worker 处理；任何身份原值禁止出现在 stdout、Issue 或 PR 证据中。

## Post-deploy verification

1. exact release SHA 健康检查通过。
2. 再运行同一脚本 `--phase post-deploy`，要求 `ok=true`。
3. 运行 identity、questionnaire、sidebar、archive、service-period 定向测试及全量 PostgreSQL CI。
4. 确认 `customer.phone_bound` 只对应 canonical bind，`missing_unionid` 权益 consumer 为 `failed_retryable`。

## Conflict handling

禁止自动 merge、按更新时间选取、按手机号猜测或 force 覆盖另一 unionid。预检只登记 conflict，不改变两侧 canonical；冲突保存在现有 `crm_user_identity_conflicts`，由后续受控人工流程处理。

## Rollback

回滚到上一精确 release SHA。当前批次无破坏性 schema migration，不删除旧表/列；回滚不恢复自动身份猜测、people-first 新写或成功假状态。
