## 当前问题

父 Epic：#67。

当前 `main@5b1f0d47f10bd74d4570f1613883dd31f9c7f93e` 的 scoped CI 可以绿色，但 `needs_full_ci` 只作为输出展示，没有调用全量回归。页面、运行时路由、OpenAPI 请求/响应、Alembic head、表 owner/lifecycle、internal-event consumer、external effect、systemd unit/timer 和 env flag 也没有统一、可重复生成的机器基线。父 Epic 记录的 34 个稳定失败来自旧基线，当前状态需要用同一 SHA 的完整 Python/PostgreSQL 与前端回归重新核对，而不能沿用口头结论或旧报告。

## 目标完成度

必须达到 L3：

- 当前完整 Python/PostgreSQL 与前端回归 0 failed；历史失败逐项归类并保留证据。
- 生成可重复的 runtime contract inventory，覆盖 page/route/OpenAPI contract、migration head、table ownership/lifecycle、job/consumer、effect、systemd unit/timer 和 env flag。
- auth、callback、payment、refund/entitlement、questionnaire、group-ops、delivery 均登记并执行 success、failure、replay/concurrency contract。
- 高风险路径 `needs_full_ci=true` 时，required CI 真正等待完整回归。
- inventory 或 contract manifest 漂移会阻断合并。

## 授权文件

- `.github/workflows/ci-fast.yml`
- `.github/workflows/full-regression.yml`
- `scripts/ci/run_architecture_gates.sh`
- `scripts/ci/select_test_scope.py`（仅在选择逻辑确需修复时）
- `scripts/ci/runtime_contract_inventory.py`（新增）
- `scripts/ci/check_high_risk_contract_inventory.py`（新增）
- `docs/ci/test_scope_manifest.yml`
- `docs/architecture/runtime_contract_inventory.json`（新增生成物）
- `docs/architecture/high_risk_contract_inventory.yml`（新增）
- `docs/cleanup/full_pytest_baseline_failure_classification.md`
- `docs/plans/2026-07-10-issue-67-systematic-optimization-design.md`
- `docs/plans/2026-07-10-issue-67-r00-baseline.md`
- `aicrm_next/router_registry.py`（仅在生成稳定 public summary 必需时）
- `deploy/production_runtime_units.json`（只读生成源；除非发现 manifest 与已存在 unit 不一致）
- `tests/test_runtime_contract_inventory.py`（新增）
- `tests/test_high_risk_contract_inventory.py`（新增）
- `tests/test_ci_workflow_contract.py`
- `tests/test_select_test_scope.py`
- 与已登记 high-risk node ID 对应的现有 contract test 文件（只允许修复确认的代码缺陷或过期测试，不得弱化断言）
- `scripts/ops/check_wecom_callback_objective_coverage.py`（仅修正过期证据节点）
- `tools/check_repository_provider_hardening.py`（仅修复 fixture 字段名误报）
- `tools/check_sql_static_guard.py`
- `aicrm_next/automation/automation_engine/group_ops/application.py`
- `aicrm_next/automation/automation_engine/group_ops/projections.py`
- `aicrm_next/service_period/repo.py`
- `aicrm_next/customer_read_model/repo.py`（仅增加 SQLite/PostgreSQL JSON 文本提取方言分支）
- `aicrm_next/customer_read_model/sql_dialect.py`（新增小型方言 helper）
- `migrations/versions/0097_service_period_unionid_cleanup.py`（新增）
- `docs/architecture/data_table_lifecycle_manifest.yml`
- `tests/conftest.py`（仅同步最终 service-period test schema）
- 基线失败清单中的过期测试文件（只允许跟随已经生效的生产合同）
- `tests/test_external_orders_customer_projection.py` 与 `tests/test_customer_live_source_repository.py`（现有方言兼容回归，不改测试语义）

## 禁止范围

- 不新增用户功能、页面、菜单、业务 route、业务表、业务指标或独立服务。
- 不修改现有成功业务语义，不增加 operator 操作步骤。
- 不启用任何新的真实外呼，不修改生产 token/secret/execution mode。
- 不修改已发布 Alembic revision；本切片唯一 schema 变更是新增 `0097` 删除违反已有 unionid 最终 schema 门禁的 `service_period_entitlements.mobile_snapshot`。
- 不借 R00 扩展 R01-R15 业务能力；仅修复导致 R00 全回归无法归零的确认缺陷与过期测试。
- 不使用 fixture-only route/data 伪装生产 inventory。

## 实现方向

从真实 FastAPI composition root/OpenAPI 和现有治理 manifest 生成稳定 JSON；AST 只提取字面量 env key，不读取 secret 值。高风险 contract manifest 记录 pytest node ID、owner、CI scope 和真实外呼期望，checker 验证节点存在且 scope 覆盖完整。`Full Regression` 增加 `workflow_call`，`CI Fast` 在 `needs_full_ci=true` 时调用并把结果加入最终 required job。inventory checker 安装进 full architecture gate。

## 定向测试

```bash
python -m pytest tests/test_runtime_contract_inventory.py -q
python -m pytest tests/test_high_risk_contract_inventory.py -q
python -m pytest tests/test_ci_workflow_contract.py tests/test_select_test_scope.py -q
python -m pytest tests/test_router_registry_contract.py tests/test_alembic_revision_chain.py tests/test_background_job_contract.py tests/test_external_effects_boundary.py -q
bash scripts/ci/run_architecture_gates.sh --mode full
```

高风险 manifest 中登记的所有 success/failure/replay-concurrency node ID 必须在 PostgreSQL test mode 下逐项通过。

## 全量与架构门禁

```bash
DATABASE_URL=postgresql://test:test@localhost:5432/test AICRM_PYTEST_FIXTURE_DEFAULT=1 python -m pytest tests/ -n auto --dist=loadfile -v --tb=short --timeout=120 --timeout-method=thread
npm ci
npm run typecheck
npm run build:frontend
git diff --exit-code
npm run test:frontend:all
bash scripts/ci/run_architecture_gates.sh --mode full
```

PR 必须证明高风险文件改动自动触发 reusable Full Regression，且该结果为 required result 的依赖。

## 数据迁移与对账

`0097_service_period_unionid_cleanup` 仅删除可由 `crm_user_identity` 按 unionid 读取的重复手机号列，不删除 entitlement 或 event 数据。上线前对账列存在性和 Alembic head；上线后验证最终 schema 不再包含该列、成员手机号仍来自 `crm_user_identity`。inventory 生成两次必须一致；fixture-only 数据不得进入生产事实口径。

## 风险与回滚

主要风险是 app import 副作用、inventory 非确定性、GitHub reusable workflow 条件错误、full CI 执行时间增加，以及服务期成员手机号读取源切换到统一身份表。生成器不得连接生产数据库或执行外部调用。回滚顺序为先执行 `alembic downgrade 0096_admin_wecom_directory_members`恢复空 snapshot 列，再 revert R00 PR；不修改 production env 或 external effect mode。

## 验收

- [ ] 当前完整 Python/PostgreSQL 与前端回归 0 failed。
- [ ] 历史失败均属于 `代码缺陷 / 测试过期 / 环境缺失 / 能力已退休` 之一，未删除失败证据。
- [ ] runtime contract inventory 可生成、可检查、稳定排序，二次生成无 diff。
- [ ] page/route/OpenAPI、migration、table、job/consumer、effect、unit/timer、env flag 均在 inventory 中。
- [ ] 七个高风险域均有 success/failure/replay-concurrency 节点并实际通过。
- [ ] 修改 callback/payment/refund/adapter 等高风险文件会触发 full regression。
- [ ] `needs_full_ci=true` 时 full Python/PostgreSQL 和 full frontend 均为 required CI 路径。
- [ ] deliberate inventory/manifest drift 会使 checker 非零退出。
- [ ] User-visible capability delta: none。
- [ ] New product route/page/menu: none。
- [ ] New business metric/model: none。
- [ ] Existing behavior changed: yes（仅服务期手机号从重复 snapshot 改为按 unionid 读取统一身份表，对外字段不变）。
- [ ] Security breaking change: no。
- [ ] Old path removed in this PR: no。
- [ ] Rollback path: revert R00 PR。
