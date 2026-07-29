# Architecture

## Why Modular Monolith

The next backend starts as a modular monolith because AI-CRM is one tightly integrated product: customer identity, read model, User Ops, automation conversion, MCP, payments, and admin configuration must share stable product semantics. Splitting services now would add distributed failure modes before the new contracts are proven.

The monolith is modular at source boundaries: each context owns API, application use cases, domain rules, and repo ports. PostgreSQL is the target persistence model. User Ops and Customer Read Model now both have a default fixture repository and a PostgreSQL-ready SQLAlchemy repository behind the same application boundary.

## Bounded Contexts

- `platform_foundation`: config, auth, audit, idempotency, health, database runtime.
- `integration_gateway`: MCP, WeCom, OpenClaw, payment and media integration ports.
- `identity_contact`: people, external contacts, mobile/openid/unionid resolution, owner/follow user snapshots.
- `customer_read_model`: customer list/detail/timeline/chat-context projections.
- `ops_enrollment`: User Ops pool, enrollment status, batch-send eligibility, do-not-disturb.
- `questionnaire`: admin questionnaire, public H5 submission, OAuth binding, external push.
- `automation_engine`: six-pool conversion state machine, workflows, operation tasks, execution records.
- `ai_assist`: AI Customer Pulse, Followup Orchestrator, Cloud Orchestrator actions.

## Allowed Dependency Direction

- API modules call only application use cases.
- Application modules orchestrate their own domain/repo and call other contexts through application-level interfaces.
- Domain modules hold business rules and do not import API modules.
- Repo modules own data access and can be swapped from fixture to PostgreSQL through explicit factory functions.
- `integration_gateway` can call `customer_read_model`, `identity_contact`, `automation_engine`, and `platform_foundation` through application APIs.
- `customer_read_model` can read from formal context ports, but must not perform writes.

## Forbidden Dependency Direction

- No code in `experiments/ai_crm_next` may import `wecom_ability_service.*`.
- No code in `experiments/ai_crm_next` may import `openclaw_service.*`.
- API modules must not import old service facades or raw database wrappers.
- MCP must not access repositories directly; it dispatches to application use cases.
- Read models must not execute writes.
- Backend context naming must not leak into frontend visible labels.
- 渠道码中心的新建、编辑和保存必须基于 AI-CRM Next 原生路由、模板和数据访问实现；旧版 `siyuan-crm` / Flask 能力只能作为迁移参考，历史 `automation_channel` 数据要被完整承接，不能用 Next 兼容占位页替代。

## Avoiding the Old `service.py` Shape

The old backend accumulated broad facade modules because HTTP, integration, read aggregation, and business rules could meet in one place. This experiment prevents that by:

- one bounded context per package;
- explicit application classes such as `ListCustomersQuery`, `ResolvePersonIdentityQuery`, and `ExecuteUserOpsBatchSendCommand`;
- repository interfaces behind application classes, with fixture and SQLAlchemy implementations where a context is ready;
- route modules that parse requests and serialize responses only;
- tests that reject imports of old backend packages.

## User Ops Persistence Boundary

`ops_enrollment` keeps User Ops persistence behind `UserOpsRepository`.

- Default runtime: `InMemoryUserOpsRepository`, so the experiment still starts without a database.
- PostgreSQL-ready implementation: `SqlAlchemyUserOpsRepository`, using SQLAlchemy table definitions registered on `Base.metadata`.
- Migration: `docs/archive/experiments_ai_crm_next/workspace/migrations/versions/0001_user_ops_postgresql_ready.py` creates User Ops pool, do-not-disturb, and send-record tables.
- Switch point: `build_user_ops_repository` reads `USER_OPS_REPO_BACKEND=memory|sqlalchemy`.

The API layer does not import SQLAlchemy models or sessions. It calls application queries/commands only. Execute still uses the fake `integration_gateway` dispatch adapter; no real Enterprise WeChat send is triggered in this slice.

## Customer Read Model Persistence Boundary

`customer_read_model` keeps customer list, detail snapshots, timeline events, and recent messages behind `CustomerReadRepository`.

- Default runtime: `InMemoryCustomerReadModelRepository`, so the copied customer center frontend and MCP tools still start without a database.
- PostgreSQL-ready implementation: `SqlAlchemyCustomerReadModelRepository`, using SQLAlchemy table definitions registered on `Base.metadata`.
- Migration: `docs/archive/experiments_ai_crm_next/workspace/migrations/versions/0002_customer_read_model_postgresql_ready.py` creates customer list index, detail snapshot, timeline event, and recent-message tables.
- Switch point: `build_customer_read_model_repository` reads `CUSTOMER_READ_MODEL_REPO_BACKEND=memory|sqlalchemy`.
- Parity guard: `aicrm_next/customer_read_model/parity_spec.py` and `retired experiment wrapper; see docs/archive/experiments_ai_crm_next/retired_tools.md` lock the old OpenClaw-compatible read contract.

The API layer does not import SQLAlchemy models or sessions. MCP calls Customer Read Model application queries through `integration_gateway`; it does not access repositories directly. This slice remains `partial`: it has not connected to production PostgreSQL and has not replaced the old Flask Customer Center.

## Questionnaire Boundary

`questionnaire` owns admin questionnaire contracts, public H5 read/submit contracts, fake WeChat OAuth callback contracts, and fixture submission storage.

- API layer parses FastAPI requests and calls application queries/commands.
- Application layer orchestrates questionnaire repository, scoring/tag-id derivation, fake OAuth adapter, and `identity_contact.ResolvePersonIdentityQuery`.
- Repository layer is fixture/in-memory in this first slice.
- Submit does not write Customer Read Model and does not mutate User Ops directly. It now emits the automation questionnaire result through `automation_engine.ApplyQuestionnaireResultCommand`, keeping the cross-context handoff at the application boundary.
- OAuth uses `FakeWechatOAuthAdapter`; it never calls WeChat.
- Selected `tag_codes` are stored as strings; no real WeCom tagging/contact API or external webhook is called.
- `aicrm_next/questionnaire/parity_spec.py` and `retired experiment wrapper; see docs/archive/experiments_ai_crm_next/retired_tools.md` lock the first admin/public/submit/preflight contract.

This slice remains `partial`: it has not replaced the old Flask questionnaire system and has no production PostgreSQL schema yet.

## Automation Engine Boundary

The old Automation Conversion state-machine/readonly slice has been retired.
`/admin/automation-conversion` is now owned by AI Audience, and the old
automation_program/runtime-v2 fixtures, parity tooling, and gray smoke tooling
are no longer a migration target.

## PostgreSQL Integration-Test Boundary

PostgreSQL integration tests are intentionally outside the ordinary test path. They are marked `postgres_integration` and skip unless `AICRM_NEXT_TEST_DATABASE_URL` is set.

`aicrm_next/platform/shared/postgres_test_guard.py` refuses unsafe database URLs:

- empty URLs;
- non-PostgreSQL URLs;
- non-local hosts;
- database names without a visible `test` marker.

The runner script prints only a redacted URL. These tests validate Alembic migrations and SQLAlchemy repositories against a real local PostgreSQL test database; they do not connect to production PostgreSQL and do not trigger real WeCom sends.

## Commerce Boundary

`commerce` owns product catalog contracts, fake checkout, fake payment notify/return, order status, and admin transaction read models.

- API layer parses requests and calls commerce application queries/commands.
- Application layer validates checkout/product operations and calls fake provider adapters.
- `payment_adapters.py` is fixture-only and never calls WeChat Pay or Alipay.
- Repository layer remains in-memory/fixture in this slice.
- Notify creates or updates local payment records only; it does not emit real webhooks, WeCom actions, or automation side effects.
- `aicrm_next/commerce/parity_spec.py` and `retired experiment wrapper; see docs/archive/experiments_ai_crm_next/retired_tools.md` lock product/checkout/transaction shape.

This slice remains `partial`, not a production payment implementation.

## Media Library Boundary

`media_library` owns image, attachment, and mini-program material contracts.

- API layer parses requests and calls media application commands/queries.
- Repository layer stores fixture data only.
- from-url/from-base64 image import returns fake imported records; it does not fetch remote content or upload to cloud storage.
- Mini-program thumbnail references are plain ids in this slice; no WeCom media upload or resolver is called.
- `aicrm_next/engagement/media_library/parity_spec.py` and `retired experiment wrapper; see docs/archive/experiments_ai_crm_next/retired_tools.md` lock image/attachment/miniprogram list shapes.

This slice remains `partial`, not a production material-storage implementation.
