# API Contracts

First slice contracts intentionally mirror the current AI-CRM stable surfaces.

## Health

### `GET /health`

Returns:

- `ok`
- `status`
- `service`
- `database`

### `GET /api/system/health`

Same envelope as `/health`.

## Customer Read Model

Status: `partial`.

Customer Center and Customer Timeline now expose a deeper legacy/OpenClaw-compatible adapter. The default runtime is still fixture/in-memory, while a PostgreSQL-ready SQLAlchemy repository and Alembic schema now cover customer list, detail snapshots, timeline events, and recent messages. API envelopes, filters, drawer-support fields, recent-message shape, MCP read context, and parity tooling are covered by tests. This is still not connected to a production database and does not replace the old Flask customer center yet.

Persistence and parity status:

- Default runtime: `InMemoryCustomerReadModelRepository`.
- PostgreSQL-ready storage: `SqlAlchemyCustomerReadModelRepository` with `customer_list_index_next`, `customer_detail_snapshot_next`, `customer_timeline_event_next`, and `customer_recent_message_next`.
- Switching point: `CUSTOMER_READ_MODEL_REPO_BACKEND=memory|sqlalchemy` through `build_customer_read_model_repository`.
- Parity comparison: `retired experiment wrapper; see docs/archive/experiments_ai_crm_next/retired_tools.md` compares anonymized old fixtures or old Flask HTTP responses against AI-CRM Next.
- PostgreSQL integration tests: explicit `postgres_integration` tests can validate Alembic upgrade/downgrade and SQL repositories against a guarded local test database.
- Status remains `partial`; this is not a fully implemented production Customer Center backend.

### `GET /api/customers`

Top-level fields:

- `ok`
- `customers`
- `items`
- `count`
- `total`
- `limit`
- `offset`
- `filters`

Customer item fields:

- `external_userid`
- `customer_name`
- `owner_userid`
- `owner_display_name`
- `mobile`
- `is_bound`
- `binding_status`
- `tags`
- `class_user_status`
- `last_message_at`
- `last_touch_at`
- `updated_at`

Supported filters:

- `owner_userid`
- `tag`
- `status`
- `is_bound`
- `mobile`
- `keyword`
- `limit`
- `offset`

### `GET /api/customers/{external_userid}`

Top-level fields:

- `ok`
- `customer`

Customer fields include:

- `external_userid`
- `customer_name`
- `owner_userid`
- `owner_display_name`
- `remark`
- `description`
- `mobile`
- `is_bound`
- `binding_status`
- `follow_user_userids`
- `last_message_at`
- `last_touch_at`
- `updated_at`
- `tags`
- `class_user_status`
- `binding`
- `identity`
- `follow_users`
- `marketing_summary`
- `marketing_profile`
- `contact`
- `sidebar_context`

### `GET /api/customers/{external_userid}/timeline`

Top-level fields:

- `ok`
- `timeline`

Timeline fields:

- `external_userid`
- `items`
- `count`
- `limit`
- `offset`
- `filters`
- `total`

Item fields:

- `event_id`
- `event_type`
- `event_time`
- `title`
- `summary`
- `source_table`
- `source_id`
- `metadata`

### `GET /api/messages/{external_userid}/recent`

Status: `partial`.

Returns:

- `ok`
- `messages`

Message item fields:

- `msgid`
- `msgtype`
- `content`
- `send_time`
- `external_userid`

This endpoint powers the OpenClaw/MCP recent-chat read path. Unknown customers return HTTP 404. It does not fall back from mobile to `external_userid`.

## User Ops

Status: `partial`.

This slice implements the User Ops pool projection, shared filters, manual/auto do-not-disturb composition, batch-send preview, fake-dispatch execute, and send-record readback. Default app runtime still uses the in-memory fixture repository, while a PostgreSQL-ready SQLAlchemy repository and Alembic schema now cover the same repository contract in tests. It does not connect to a production PostgreSQL database and does not call real WeCom.

Persistence status:

- Default runtime: `InMemoryUserOpsRepository`.
- PostgreSQL-ready storage: `SqlAlchemyUserOpsRepository` with `user_ops_pool_current_next`, `user_ops_do_not_disturb_next`, and `user_ops_send_records_next`.
- Switching point: `USER_OPS_REPO_BACKEND=memory|sqlalchemy` through `build_user_ops_repository`.
- Parity comparison: `retired experiment wrapper; see docs/archive/experiments_ai_crm_next/retired_tools.md` can compare old Flask HTTP responses or anonymized old fixtures against AI-CRM Next.
- PostgreSQL integration tests: explicit `postgres_integration` tests can validate Alembic upgrade/downgrade and SQL repositories against a guarded local test database.
- Status remains `partial`; this is not a fully implemented production User Ops backend.

### `GET /api/admin/user-ops/overview`

Status: `partial`.

Returns `ok`, `filters`, `cards`, `generated_at`, `metrics`, and `class_term_options`.

The 8 fixed cards are:

- 引流品总数
- 已加微
- 未加微
- 已绑手机号
- 未绑手机号
- 黄小璨已激活
- 黄小璨未激活
- 激活待录入

### `GET /api/admin/user-ops/list`

Returns `ok`, `items`, `total`, `count`, `limit`, `offset`, `filters`, `filter_options`, and `meta`.

Items preserve:

- `id`
- `mobile`
- `external_userid`
- `customer_name`
- `owner_userid`
- `owner_display_name`
- `class_term_no`
- `class_term_label`
- `source_type`
- `created_at`
- `updated_at`
- `is_added_wecom`
- `is_wecom_added`
- `is_mobile_bound`
- `activation_bucket`
- `activation_bucket_label`
- `huangxiaocan_activation_state`
- `huangxiaocan_activation_state_label`
- `do_not_disturb`
- `do_not_disturb_reasons`
- `can_open_customer_detail`
- `can_batch_send`

## Questionnaire / WeChat OAuth

Status: `partial`.

This slice establishes the first questionnaire migration contract. It is fixture-backed and does not replace the old Flask questionnaire system. It does not call real WeChat OAuth, WeCom tagging/contact APIs, or external webhooks.

### Admin APIs

Implemented paths:

- `GET /admin/questionnaires`
- `GET /admin/questionnaires/ui`
- `GET /api/admin/questionnaires`
- `GET /api/admin/questionnaires/{questionnaire_id}`
- `POST /api/admin/questionnaires`
- `PUT /api/admin/questionnaires/{questionnaire_id}`
- `POST /api/admin/questionnaires/{questionnaire_id}/disable`
- `POST /api/admin/questionnaires/{questionnaire_id}/enable`
- `DELETE /api/admin/questionnaires/{questionnaire_id}`
- `GET /api/admin/questionnaires/{questionnaire_id}/export`
- `GET /api/admin/questionnaires/preflight`
- `GET /api/admin/questionnaires/{questionnaire_id}/latest-submit-debug`

`GET /api/admin/questionnaires` returns:

- `ok`
- `items`
- `questionnaires`
- `total`
- `limit`
- `offset`

Each item preserves:

- `id`
- `slug`
- `title`
- `description`
- `enabled`
- `redirect_url`
- `created_at`
- `updated_at`
- `question_count`

`GET /api/admin/questionnaires/{questionnaire_id}` returns:

- `ok`
- `questionnaire`
- `questions`
- `external_push_config`

`preflight` returns `ok`, `checks`, and these check keys:

- `wechat_oauth_configured`
- `wecom_contact_configured`
- `debug_session_api_enabled`
- `questionnaire_admin_ui_enabled`
- `wecom_tags_api_available`
- `identity_map_available`

### Public H5 APIs

Implemented paths:

- `GET /s/{slug}`
- `GET /api/h5/questionnaires/{slug}`
- `POST /api/h5/questionnaires/{slug}/submit`
- `GET /api/h5/questionnaires/{slug}/result/{submission_id}`

`POST /api/h5/questionnaires/{slug}/submit` accepts `answers` and `respondent_identity` with optional `mobile`, `external_userid`, `openid`, and `unionid`.

Submit returns:

- `ok`
- `submission_id`
- `questionnaire_id`
- `slug`
- `external_userid`
- `person_id`
- `score`
- `final_tags`
- `redirect_url`
- `result_message`

Required-answer validation returns HTTP 400. Missing or disabled questionnaire returns HTTP 404 in this first slice. `final_tags` stores selected tag ids only; no real WeCom tagging is triggered.

### WeChat OAuth Stub

Implemented paths:

- `GET /api/h5/wechat/oauth/start`
- `GET /api/h5/wechat/oauth/callback`

The adapter is fake/stubbed. Callback returns:

- `ok`
- `openid`
- `unionid`
- `external_userid`
- `redirect_url`
- `source_status`

`source_status` is `fake` for supplied state and `missing_config` when callback lacks enough fake state. Real WeChat OAuth exchange is not implemented.

### Parity Tooling

- Spec: `aicrm_next/questionnaire/parity_spec.py`
- Fixtures: `tests/fixtures/old_questionnaire/`
- Tool: `retired experiment wrapper; see docs/archive/experiments_ai_crm_next/retired_tools.md`

The default comparison uses anonymized fixtures and a fixture-safe submit payload. Do not run real submit against old production.

### `POST /api/admin/user-ops/do-not-disturb`

Status: `partial`.

This endpoint keeps manual do-not-disturb state through the User Ops repository contract and composes manual reasons with auto reasons. It is covered by both in-memory and SQLAlchemy repository tests, but default runtime is still in-memory and production PostgreSQL is not connected.

Returns:

- `ok`
- `target`
- `do_not_disturb`
- `do_not_disturb_reasons`

Errors:

- HTTP 400 when neither `external_userid` nor `mobile` is supplied.
- HTTP 404 when the target is not present in the fixture User Ops pool.

### `POST /api/admin/user-ops/batch-send/preview`

Status: `partial`.

Returns:

- `filters`
- `selected_count`
- `eligible_count`
- `skipped_count`
- `skipped_by_reason`
- `skipped_summary`
- `include_do_not_disturb`
- `owner_buckets`
- `sender_buckets`
- `sendable_samples`
- `final_targets`
- `content_preview`
- `image_count`
- `has_body`

### `POST /api/admin/user-ops/batch-send/execute`

Status: `partial`.

Requires `confirm=true`. Without confirmation it returns HTTP 400.

Confirmed execution uses `integration_gateway.DispatchGateway` with a fake WeCom adapter. It returns preview fields plus:

- `record_id`
- `sent_count`
- `execution_summary`
- `task_results`

### `GET /api/admin/user-ops/send-records`

Status: `partial`.

Returns `items`, `records`, `count`, `total`, `limit`, and `offset`.

Record summaries include:

- `id`
- `record_id`
- `task_type`
- `selected_count`
- `eligible_count`
- `sent_count`
- `skipped_count`
- `skipped_reasons`
- `include_do_not_disturb`
- `content_preview`
- `image_count`
- `sender_userids`
- `filter_snapshot`
- `operator`
- `status`
- `status_label`
- `created_at`

### `GET /api/admin/user-ops/send-records/{record_id}`

Status: `partial`.

Returns `record`, `task_results`, `delivery_status_supported=false`, and `status_note`.

### Legacy User Ops frontend adapter stubs

Status: `stubbed`.

The copied `admin_user_ops.html` also references these legacy paths. They are present only to avoid broken controls during frontend parity smoke testing:

- `POST /api/admin/user-ops/send-records/{record_id}/refresh`
- `GET /api/admin/user-ops/export`
- `GET /api/admin/miniprogram-library`

## Retired Automation Conversion

Status: `retired`.

Old Automation Conversion program, pool, member, state-transition, activation
webhook, fake OpenClaw push, execution-record, and Runtime V2 APIs are no longer
active API contracts. `/admin/automation-conversion` now belongs to
`ai_audience_ops` and renders the AI Audience package list. The admin read API
for that page is `/api/admin/ai-audience/packages`.

## MCP

### `GET /mcp`

Returns transport metadata.

### `POST /mcp`

JSON-RPC methods:

- `initialize`
- `tools/list`
- `tools/call`

First-slice tools:

- `resolve_customer`
- `get_customer_context`
- `get_recent_messages`

Customer refs support `customer_ref`, `external_userid`, `limit`, `recent_message_limit`, and `timeline_limit`. Mobile lookup failures return explicit JSON-RPC errors.

## Commerce / Payment

Status: `partial`.

This slice establishes the first product, fake checkout, fake notify, order-status, and transaction-management contracts. It remains fixture/in-memory only. It does not call real WeChat Pay, Alipay, WeCom, cloud storage, external webhooks, or production PostgreSQL.

### Product Management

Admin/frontend routes:

- `GET /admin/wechat-pay/products`
- `GET /api/admin/wechat-pay/products`
- `GET /api/admin/wechat-pay/products/{product_id}`
- `POST /api/admin/wechat-pay/products`
- `PUT /api/admin/wechat-pay/products/{product_id}`
- `POST /api/admin/wechat-pay/products/{product_id}/enable`
- `POST /api/admin/wechat-pay/products/{product_id}/disable`
- `DELETE /api/admin/wechat-pay/products/{product_id}`

Product item fields:

- `id`
- `product_code`
- `title`
- `description`
- `price_cents`
- `currency`
- `enabled`
- `page_slug`
- `cover_image_id`
- `detail_image_ids`
- `buy_button_text`
- `created_at`
- `updated_at`

Product detail also includes `detail_sections`. `price_cents` must be a non-negative integer. `product_code` is unique. Delete is a first-slice soft delete.

### Public Product / Checkout

Routes:

- `GET /p/{page_slug}`
- `GET /api/products/{page_slug}`
- `POST /api/checkout/wechat`
- `POST /api/checkout/alipay`
- `GET /api/orders/{order_no}`
- `GET /api/orders/{order_no}/status`

Checkout returns `ok`, `order_no`, `payment_provider`, `amount_cents`, `payment_status`, `checkout_url`, `qr_code_url`, `provider_payload`, and `fake_payment=true`.

Disabled products cannot checkout. Unknown products return 404. Invalid quantity returns 400.

### Fake Notify / Return

Routes:

- `POST /api/wechat-pay/notify`
- `POST /api/alipay/notify`
- `GET /api/alipay/return`

Notify returns `ok`, `order_no`, `payment_provider`, `payment_status`, `transaction_id`, and `source_status`. Signature verification is not implemented. Duplicate notify is idempotent for the same order/status. Payment events are stored as local fixture records only and do not trigger real automation or WeCom side effects.

### Admin Transactions

Routes:

- `GET /admin/wechat-pay/transactions`
- `GET /api/admin/wechat-pay/transactions`
- `GET /api/admin/wechat-pay/transactions/{order_no}`
- `GET /admin/alipay/transactions`
- `GET /api/admin/alipay/transactions`
- `GET /api/admin/alipay/transactions/{order_no}`

Transaction item fields:

- `order_no`
- `payment_provider`
- `product_code`
- `product_title`
- `buyer_mobile`
- `external_userid`
- `amount_cents`
- `currency`
- `payment_status`
- `transaction_id`
- `paid_at`
- `created_at`
- `updated_at`

Filters supported in this slice: `payment_status`, `product_code`, `mobile`, `external_userid`, `date_from`, `date_to`, `limit`, and `offset`.

## Media Library

Status: `partial`.

This slice establishes image, attachment, and mini-program material contracts. It remains fixture/in-memory only. It does not upload to cloud storage or WeCom media.

### Image Library

Routes:

- `GET /admin/image-library`
- `GET /api/admin/image-library`
- `POST /api/admin/image-library`
- `POST /api/admin/image-library/from-url`
- `POST /api/admin/image-library/from-base64`
- `GET /api/admin/image-library/{image_id}`
- `PUT /api/admin/image-library/{image_id}`
- `DELETE /api/admin/image-library/{image_id}`

Image item fields: `id`, `name`, `file_name`, `content_type`, `file_size`, `width`, `height`, `data_url`, `tags`, `created_at`, and `updated_at`.

### Attachment Library

Routes:

- `GET /admin/attachment-library`
- `GET /api/admin/attachment-library`
- `POST /api/admin/attachment-library`
- `GET /api/admin/attachment-library/{attachment_id}`
- `PUT /api/admin/attachment-library/{attachment_id}`
- `DELETE /api/admin/attachment-library/{attachment_id}`

Attachment item fields: `id`, `name`, `file_name`, `mime_type`, `file_size`, `data_base64`, `tags`, `enabled`, `created_at`, and `updated_at`.

### Mini-program Library

Routes:

- `GET /admin/miniprogram-library`
- `GET /api/admin/miniprogram-library`
- `POST /api/admin/miniprogram-library`
- `GET /api/admin/miniprogram-library/{item_id}`
- `PUT /api/admin/miniprogram-library/{item_id}`
- `DELETE /api/admin/miniprogram-library/{item_id}`

Mini-program item fields: `id`, `title`, `appid`, `page_path`, `thumb_image_id`, `description`, `tags`, `enabled`, `created_at`, and `updated_at`.

## Commerce / Media Parity Tooling

- Spec: `aicrm_next/commerce/parity_spec.py`, `aicrm_next/engagement/media_library/parity_spec.py`
- Fixtures: `tests/fixtures/old_commerce/`, `tests/fixtures/old_media_library/`
- Tools: `retired experiment wrapper; see docs/archive/experiments_ai_crm_next/retired_tools.md`, `retired experiment wrapper; see docs/archive/experiments_ai_crm_next/retired_tools.md`
- Commerce `--old-base-url` mode defaults to read-only endpoints and skips checkout writes with `old_write_endpoint_disabled`.
- Checkout parity is exercised with old fixtures and AI-CRM Next fake checkout; it must not POST checkout against old production by default.

Both slices are `partial`, not implemented production replacements.
