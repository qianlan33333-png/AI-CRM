# Migration Strategy

## Principles

- Path names remain stable.
- Core JSON fields remain stable.
- Frontend experience remains stable.
- The new backend supports the old frontend through compatible APIs and adapters.
- Old Flask and new FastAPI can run in parallel until contract verification is complete.

## Phases

1. Contract fixture slice: keep fixture repos but lock customer read model, MCP, and User Ops response envelopes.
2. Customer Center adapter slice: deepen `/api/customers`, detail, timeline, and recent-message read paths while keeping the copied customers template unchanged. Current status: partial adapter with OpenClaw read fields, PostgreSQL-ready repository, and parity tooling covered; default runtime remains in-memory.
3. User Ops partial slice: deepen pool projection, filters, DND, preview, fake-dispatch execute, and send records without changing the copied old frontend.
4. PostgreSQL-ready repo slice: define SQLAlchemy 2.x tables/repositories and Alembic migrations while keeping API envelopes unchanged. Current User Ops and Customer Read Model status is here: SQL repo parity tests exist, default runtime is still in-memory, and no production database is connected.
5. PostgreSQL integration-test slice: run Alembic upgrade/downgrade plus User Ops and Customer Read Model SQLAlchemy repositories against an explicitly supplied local PostgreSQL test database. This is guarded by `AICRM_NEXT_TEST_DATABASE_URL` and does not run in ordinary pytest.
6. Frontend compatibility copy: copy old templates/static into the root Next package and wire them to adapter routes. Current status: admin shell templates, `admin_user_ops.html`, `admin_console/*`, and `static/admin_console/*` live under `../../aicrm_next/frontend_compat/`.
7. Dual-run validation: compare old Flask and new FastAPI responses for stable paths. Current User Ops and Customer Read Model status: compare tools support HTTP mode and anonymized old-fixture mode.
8. Questionnaire/OAuth first slice: lock admin questionnaire API, public H5 API, fake WeChat OAuth callback, fixture submit pipeline, identity-resolution boundary, old-template adapter, and questionnaire parity tooling. Current status: `partial`, no real OAuth, no real WeCom, no external webhook push, no production DB.
9. Automation Conversion first slice: lock member/pool APIs, six-pool state machine semantics, questionnaire-submit boundary, activation webhook stub, fake OpenClaw push, old-template admin adapter, and automation parity tooling. Current status: `partial`, no real WeCom, no real OpenClaw, no external webhook delivery, no production DB.
10. Write-path migration: move User Ops, questionnaire, automation, payments, and media writes context by context.
11. Cutover: route production traffic to the new app only after parity tests, UI screenshots, and operational runbooks pass.

## Adapter Before Replacement

The first production-like bridge should adapt the new application DTOs to old JSON shapes. Only after old frontend pages work unchanged should internal DTOs replace legacy shapes behind the adapter.

The copied frontend is the product baseline. New FastAPI routes must keep old paths and visible labels stable; backend modules may change behind the adapter, but navigation, table density, filters, drawers, modals, and action placement must not drift.

## Parallel Validation

Run old and new services side by side:

- compare `/api/customers`;
- compare `/api/customers/{external_userid}`;
- compare `/api/customers/{external_userid}/timeline`;
- compare `/api/messages/{external_userid}/recent`;
- compare `/api/admin/user-ops/overview`;
- compare `/api/admin/user-ops/list`;
- compare `POST /api/admin/user-ops/do-not-disturb` as partial behavior through both in-memory and SQLAlchemy repository tests before enabling production PostgreSQL;
- compare `POST /api/admin/user-ops/batch-send/preview` and `/execute`; execute remains fake-dispatch only until the integration gateway is approved for real WeCom sends;
- compare `/api/admin/questionnaires`, `/api/admin/questionnaires/{id}`, `/api/admin/questionnaires/preflight`, `/api/h5/questionnaires/{slug}`, and fixture-safe questionnaire submit;
- compare `/api/admin/automation-conversion/overview`, `/pools`, `/members`, member detail, execution records, and fixture-safe activation webhook shape;
- compare `/mcp` JSON-RPC responses.

Frontend screenshots should verify that navigation, tables, filters, drawers, modals, and button placement did not drift.

## User Ops Parity Comparison

The historical comparison wrapper is retired. See
`docs/archive/experiments_ai_crm_next/retired_tools.md` for the archived tool
index.

For live dual-run validation, replace `--old-fixture-dir` and `--next-testclient` with `--old-base-url` and `--next-base-url`. The default command avoids write endpoints; execute/DND style checks require an explicitly isolated environment and `--include-write-endpoints`.

## User Ops Repository Switch

User Ops now has a storage boundary:

- `InMemoryUserOpsRepository`: default experiment runtime and TestClient behavior.
- `SqlAlchemyUserOpsRepository`: PostgreSQL-ready implementation using `user_ops_pool_current_next`, `user_ops_do_not_disturb_next`, and `user_ops_send_records_next`.
- `build_user_ops_repository`: switch point controlled by `USER_OPS_REPO_BACKEND=memory|sqlalchemy`.

This does not mean User Ops is production-complete. The SQL repository is tested against SQLAlchemy with an in-memory database for parity, but it has not been run against a real PostgreSQL instance, has not imported historical data, and still dispatches through the fake integration gateway.

## Customer Read Model Parity Comparison

The historical comparison wrapper is retired. See
`docs/archive/experiments_ai_crm_next/retired_tools.md` for the archived tool
index.

For live dual-run validation, replace `--old-fixture-dir` and `--next-testclient` with `--old-base-url` and `--next-base-url`. The compared endpoints are read-only: `/api/customers`, `/api/customers/{external_userid}`, `/api/customers/{external_userid}/timeline`, and `/api/messages/{external_userid}/recent`.

## Customer Read Model Repository Switch

Customer Read Model now has a storage boundary:

- `InMemoryCustomerReadModelRepository`: default experiment runtime and TestClient behavior.
- `SqlAlchemyCustomerReadModelRepository`: PostgreSQL-ready implementation using `customer_list_index_next`, `customer_detail_snapshot_next`, `customer_timeline_event_next`, and `customer_recent_message_next`.
- `build_customer_read_model_repository`: switch point controlled by `CUSTOMER_READ_MODEL_REPO_BACKEND=memory|sqlalchemy`.

This does not mean Customer Center is production-complete. The SQL repository is tested against SQLAlchemy with an in-memory database for parity, but it has not been run against a real PostgreSQL instance and has not imported historical contacts, archive messages, tags, or class-user data.

## PostgreSQL Integration Tests

Real PostgreSQL integration tests are available but explicit:

```bash
AICRM_NEXT_TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/aicrm_next_test \
  .venv/bin/python -m pytest -q -m postgres_integration
```

or:

```bash
AICRM_NEXT_TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/aicrm_next_test \
  docs/archive/experiments_ai_crm_next/workspace/scripts/run_postgres_integration_tests.sh
```

Safety is enforced by `aicrm_next/platform/shared/postgres_test_guard.py`: the database URL must be local and must contain a test marker in the database name. Ordinary `.venv/bin/python -m pytest -q` skips these tests when no `AICRM_NEXT_TEST_DATABASE_URL` is provided.

These tests validate migration upgrade/downgrade and SQL repository behavior only. They do not connect to production PostgreSQL, do not import production data, and do not call real WeCom.

## Questionnaire Parity Comparison

The historical first-slice comparison wrapper is retired. See
`docs/archive/experiments_ai_crm_next/retired_tools.md` for the archived tool
index.

For live dual-run validation, replace fixtures with `--old-base-url` and `--next-base-url` only in an isolated environment. The default fixture mode includes a safe fake submit payload; do not run real submit against old production.

The questionnaire slice remains partial:

- default runtime is fixture/in-memory;
- fake OAuth adapter returns contract payloads and does not call WeChat;
- submit computes fixture score/tag ids and writes fixture submissions only;
- selected `tag_codes` are saved as strings and do not call WeCom tagging;
- external push/webhook is not sent;
- old Flask questionnaire routes are not replaced.

## Automation Conversion Parity Comparison

Retired. The old Automation Conversion fixture/parity path was removed after
`/admin/automation-conversion` moved to AI Audience and the old
automation_program/runtime-v2 route family stopped being a migration target.

The Automation Conversion slice remains partial:

- default runtime is fixture/in-memory;
- state transitions and execution records are contract-ready only;
- activation webhook is a local contract stub;
- OpenClaw push returns `delivery_status=fake` and does not send HTTP;
- real WeCom, real OpenClaw, external activation webhooks, and production PostgreSQL are not connected;
- old Flask automation routes are not replaced.

## Commerce / Media Migration Slice

Commerce and media-library migration kept historical first-slice parity tooling
as archived evidence. See
`docs/archive/experiments_ai_crm_next/retired_tools.md` for the retired wrapper
index.

This does not replace old payment or material systems. The slice remains partial:

- default runtime is fixture/in-memory;
- WeChat Pay and Alipay adapters are fake/stubbed;
- commerce `--old-base-url` parity mode defaults to read-only old-service requests and skips checkout writes with `old_write_endpoint_disabled`;
- checkout contract parity is covered through old fixtures and AI-CRM Next fake checkout, not old production POSTs;
- notify does not verify real signatures;
- payment events do not trigger real automation, WeCom, or webhooks;
- images, attachments, and mini-program materials are stored as fixture data only;
- no cloud object storage, WeCom media upload, or production PostgreSQL is connected.
