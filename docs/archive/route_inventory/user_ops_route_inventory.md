# User Ops Route Inventory

Scope: Legacy Exit group 6 moves User Ops read and preview surfaces to Next-native query/command handlers. It does not delete existing business legacy fallbacks outside User Ops, does not execute real WeCom sends, and does not run automation, payment, storage, OpenClaw, media upload, questionnaire, or sidebar JSSDK side effects.

| Route | Methods | Current owner | Runtime owner | Capability | Legacy fallback | Side effect | Status | Checker |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/admin/user-ops/ui` | GET | `aicrm_next.automation.ops_enrollment.admin_pages` | next_native page shell | real-data entry page | none | none | deletion_locked / locked | `tests/test_user_ops_admin_pages_native.py` |
| `/admin/user-ops` | GET | `aicrm_next.automation.ops_enrollment.admin_pages` | next_native page shell | admin page shell | none | none | deletion_locked / locked | `tests/test_user_ops_admin_pages_native.py` |
| `/api/admin/user-ops/overview` | GET | `aicrm_next.automation.ops_enrollment` | next_native | readonly overview | none | none | deletion_locked / locked | `tests/test_user_ops_next_queries.py` |
| `/api/admin/user-ops/cards` | GET | `aicrm_next.automation.ops_enrollment` | next_native | readonly cards | none | none | deletion_locked / locked | `tests/test_user_ops_cards_next_native.py` |
| `/api/admin/user-ops/customers` | GET | `aicrm_next.automation.ops_enrollment` | next_native | readonly customer list | none | none | deletion_locked / locked | `tests/test_user_ops_next_queries.py` |
| `/api/admin/user-ops/customers/{unionid}` | GET | `aicrm_next.automation.ops_enrollment` | next_native | drawer/profile read | none | none | deletion_locked / locked | `tests/test_user_ops_drawer_next_native.py` |
| `/api/admin/user-ops/customers/{unionid}/timeline` | GET | `aicrm_next.automation.ops_enrollment` | next_native | timeline read | none | none | deletion_locked / locked | `tests/test_user_ops_drawer_next_native.py` |
| `/api/admin/user-ops/filters` | GET | `aicrm_next.automation.ops_enrollment` | next_native | readonly filter options | none | none | deletion_locked / locked | `tests/test_user_ops_next_queries.py` |
| `/api/admin/user-ops/send-records` | GET | `aicrm_next.automation.ops_enrollment` | next_native | readonly send-record list | none | none | deletion_locked / locked | `tests/test_user_ops_next_queries.py` |
| `/api/admin/user-ops/broadcast/preview` | POST | `aicrm_next.automation.ops_enrollment` | next_command | broadcast preview only | none | `SideEffectPlan` only, `real_blocked`; empty payload is controlled default preview | deletion_locked / locked | `tests/test_user_ops_broadcast_preview.py` |
| `/api/admin/user-ops/export/preview` | POST | `aicrm_next.automation.ops_enrollment` | next_command | export preview only | none | `SideEffectPlan` only, `real_blocked`; empty payload is controlled default preview, no storage file | deletion_locked / locked | `tests/test_user_ops_export_preview.py` |
| `/api/admin/user-ops/batch-send/execute` | POST | `aicrm_next.automation.ops_enrollment` | next_native compatibility route | existing execute compatibility | out of scope | fake/blocked adapter contract only | not handled in group 6 | existing tests |
| `/api/admin/user-ops/export` | GET | `aicrm_next.automation.ops_enrollment` | next_native compatibility route | existing export stub | out of scope | none | not handled in group 6 | existing tests |

CommandBus / AuditLedger / SideEffectPlan contract:

- Preview routes execute through `CommandBus` with idempotency support via `Idempotency-Key`.
- Each preview command records an AuditLedger event.
- External work is represented only by `SideEffectPlan` records with `adapter_mode: real_blocked`, `requires_approval: true`, and `real_external_call_executed: false`.
- Broadcast preview returns candidate counts, excluded reasons, sample customers, message preview, side effect plan, `route_owner: ai_crm_next`, and `fallback_used: false`.
- Export preview returns estimated count, selected fields, masked sample, `requires_approval: true`, side effect plan, `route_owner: ai_crm_next`, and `fallback_used: false`.
- Empty broadcast/export preview payloads are accepted as controlled default previews. They return explicit `source_status` / `preview_status`, keep `fallback_used: false`, and never perform real send, external calls, or storage file generation.

Explicitly out of scope:

- Real broadcast execute.
- Automation runtime execution.
- Real WeCom send or media upload.
- OpenClaw, payment, storage file creation, questionnaire, sidebar JSSDK, or other business logic replacements.
