# Production Test Runbook

这条路径不依赖 SSH 写权限、PG 写用户或浏览器 admin cookie。生产验证优先走 External Spec API 的短期 client-credentials JWT。

## 约束

- 测试 package 使用 `prod_verify_` 前缀。
- 正式运行时业务 package 使用 `audience_` 前缀；部署脚本会确保生产 env 的
  `AICRM_AI_AUDIENCE_SPEC_ALLOWED_PREFIXES` 同时包含 `prod_verify_` 和 `audience_`。
- 测试 external_userid 只允许 `wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ`。
- 如执行真实私信，sender 只允许 `HuangYouCan`。
- 不输出 DSN、token、secret。
- 测试结束 archive 所有 `prod_verify_*` package。

## External API 创建测试包

先按 [`../auth_client_credentials.md`](../auth_client_credentials.md) 使用注册的 `external_agent`、`audience=external_integration`、`scope=write` 换取 Token，再设置 `AICRM_AUTH_CLI_ACCESS_TOKEN="$AICRM_ACCESS_TOKEN"`。

Dry-run：

```bash
python scripts/ai_audience_apply_package_spec.py docs/ai_audience/examples/questionnaire_submitted_added_wecom.md \
  --external-api-base https://www.youcangogogo.com \
  --oauth-access-token-from-env \
  --package-key-prefix prod_verify_ \
  --dry-run
```

Apply：

```bash
python scripts/ai_audience_apply_package_spec.py docs/ai_audience/examples/questionnaire_submitted_added_wecom.md \
  --external-api-base https://www.youcangogogo.com \
  --oauth-access-token-from-env \
  --package-key-prefix prod_verify_ \
  --apply \
  --confirm-production \
  --operator prod-test
```

默认不 publish、不 activate。需要发布时要求生产配置 `AICRM_AI_AUDIENCE_SPEC_ALLOW_PUBLISH=true`，再增加 `--publish`。

Archive：

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $AICRM_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  https://www.youcangogogo.com/api/external/ai-audience/packages/prod_verify_q101_submitted_added_wecom/archive \
  -d '{"operator":"prod-test"}'
```

## 验证点

- Package create/update/preview/publish/archive 通过 External Spec API。
- Members API 只返回 `nickname/external_userid/entered_at`。
- 旧 Webhook GET/PATCH 返回 `410 webhook_configuration_retired`，管理端不出现地址配置。
- Automation Binding GET/PUT/DELETE 执行双方一对一约束，并只生成受控第一方内部订阅。
- Outbound job body 只有 `external_userid[]`。
- User Ops preview/execute 只调用标准 batch-send 端口。
- 清理时通过 `DELETE /api/admin/ai-audience/packages/{id}` archive。

## 真实链路 E2E

真实 E2E 只能在生产发布包含 `/api/external/ai-audience/e2e/run` 的版本后执行。该接口默认关闭，只用于测试账号的受控验收，不是业务发送器。

固定测试边界：

- `external_userid=wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ`
- `sender_userid=HuangYouCan`
- package key 由 runner 生成，均为 `prod_e2e_*`
- 每个场景最多 1 次真实私聊发送，总量最多 5 条
- runner 结束会 archive 本轮创建的 `prod_e2e_*` package
- 响应和报告不得写入 token、cookie、DSN、secret

生产临时配置示例：

```bash
export AICRM_AI_AUDIENCE_SPEC_ALLOW_PUBLISH=true
export AICRM_AI_AUDIENCE_E2E_RUNNER_ENABLED=true

export AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED=true
export AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS=wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ
export AICRM_AI_AUDIENCE_TEST_AGENT_SENDER_USERID=HuangYouCan

export AICRM_AI_AUDIENCE_INBOUND_ACTION_EXECUTE=true
export AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY=true
export AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE=true
export AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE=true
export AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES=webhook.generic.push,wecom.message.private.send
export AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS=wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ
export AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS=HuangYouCan
```

执行：

```bash
RUN_ID="e2e_$(date +%Y%m%d_%H%M%S)"

curl -sS -X POST \
  -H "Authorization: Bearer $AICRM_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  https://www.youcangogogo.com/api/external/ai-audience/e2e/run \
  -d "{
    \"run_id\":\"${RUN_ID}\",
    \"external_userid\":\"wmbNXyCwAAXhagLBNjtlFj2jbQevWinQ\",
    \"sender_userid\":\"HuangYouCan\",
    \"scenarios\":[\"questionnaire\",\"payment\",\"channel_entry\",\"dedupe\",\"sender_whitelist\",\"user_ops_batch_send\"],
    \"confirm_real_send\":true,
    \"operator\":\"prod-e2e\"
  }" | tee "ai_audience_real_e2e_test_${RUN_ID}.json"
```

执行后必须恢复临时开关：

```bash
unset AICRM_AI_AUDIENCE_E2E_RUNNER_ENABLED
unset AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED
unset AICRM_AI_AUDIENCE_INBOUND_ACTION_EXECUTE
```

若任一步返回非测试用户、非 `HuangYouCan` sender、或 package 未 archive，结论必须判定为失败。
