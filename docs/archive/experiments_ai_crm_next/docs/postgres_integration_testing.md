# PostgreSQL Integration Testing

## Purpose

These tests prove AI-CRM Next migrations and SQLAlchemy repositories can run on a real PostgreSQL test database. They do not connect to production, do not import old Flask code, do not migrate real data, and do not call real WeCom.

普通测试仍然不依赖 PostgreSQL:

```bash
.venv/bin/python -m pytest -q
```

PostgreSQL integration tests are explicit only:

```bash
AICRM_NEXT_TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/aicrm_next_test \
  .venv/bin/python -m pytest -q -m postgres_integration
```

Or:

```bash
AICRM_NEXT_TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/aicrm_next_test \
  docs/archive/experiments_ai_crm_next/workspace/scripts/run_postgres_integration_tests.sh
```

## Safety Guard

The root source package file `aicrm_next/platform/shared/postgres_test_guard.py` validates `AICRM_NEXT_TEST_DATABASE_URL` before integration tests run.

Rules:

- URL must be non-empty.
- URL must use a PostgreSQL driver.
- host must be local: `localhost`, `127.0.0.1`, or `::1`.
- database name must contain a visible test marker such as `test` or `aicrm_next_test`.
- logs use a redacted URL and must not print the password.

If any rule fails, tests fail before connecting.

Never point `AICRM_NEXT_TEST_DATABASE_URL` at a production host or production database.

## What Is Covered

Alembic:

- `upgrade head` creates User Ops and Customer Read Model tables.
- `downgrade base` removes those experiment tables.

User Ops:

- `SqlAlchemyUserOpsRepository` seed/list/filter behavior.
- overview cards.
- manual do-not-disturb enable/cancel.
- batch-send preview skip reasons.
- send record create/list/detail.
- fake dispatch data only; no real WeCom call.

Customer Read Model:

- `SqlAlchemyCustomerReadModelRepository` seed/list/filter behavior.
- detail snapshot read.
- timeline filter and pagination.
- recent messages.
- SQL repo shape alignment with the in-memory repo.

## Current Status

User Ops and Customer Read Model remain `partial`. They are PostgreSQL-integration-test-ready, but the default app runtime is still in-memory/fixture. Production PostgreSQL is not connected, historical data is not imported, and the old Flask Customer Center/User Ops have not been replaced.
