# 当前 PostgreSQL 回归测试

## 边界

PostgreSQL 16 回归只在 GitHub Actions 的单 runner 中运行，或由维护者在明确准备的临时测试库上人工验收。日常 Mac 开发执行 `make preflight`，不会安装、启动或连接 PostgreSQL、Docker 或虚拟机。

测试数据库必须通过 `validate_postgres_test_database_url`：主机只能是本机地址，数据库名必须包含 `test`。测试不得连接生产库，也不得复制生产数据。

## 当前覆盖

- `tests/postgres/test_migration_head.py`：正式 `bootstrap_database.py` 路径从空库应用 baseline 与 176 个 migration，并到达唯一 current head。
- `tests/postgres/test_current_schema.py`：验证当前领域表、身份字段、队列字段、索引和数据库约束。
- `tests/postgres/test_repository_transactions.py`：验证事务 rollback、commit 可见性和参数转换。
- `tests/postgres/test_external_effect_idempotency.py`：验证并发创建只留下一个 durable external effect。
- `tests/release/test_migration_head.py`：main exact-SHA release gate 再次确认数据库处于行为清单声明的 head。

新增数据库行为时，测试必须放在 `tests/postgres/`，使用真实 PostgreSQL 语义和当前 repository，不得增加 SQLite 替代路径。

## 执行

普通 PR 由选择器仅在当前行为映射需要时运行相关 PostgreSQL 测试。migration、schema、支付、鉴权、企微 callback、外部副作用和部署改动会升级为同一 runner 上的完整新套件。

人工完整回归使用：

```bash
gh workflow run full-regression.yml --ref <branch>
```

若维护者已经有一个专用空测试库，可显式运行：

```bash
AICRM_TEST_DATABASE_URL=postgresql://test:test@127.0.0.1:5432/aicrm_test \
  .venv/bin/python -m pytest tests/postgres -q
```

这条人工命令不是日常开发前置，也不能作为生产数据或生产运行证据。
