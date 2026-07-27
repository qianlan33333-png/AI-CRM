# Messages Route Inventory

Status: Legacy Exit group 3 broad wildcard deleted and locked.

Scope: `/api/messages*` only. The broad production_compat wildcard has been removed after exact-route validation; known message surfaces are owned by exact Next routes.

| Path | Method | Caller | Current owner | Expected owner | Read/write | External side effect risk | Replacement decision | Delete decision | Test coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/api/messages/{external_userid}/recent` | GET | `aicrm_next/crm/customer_read_model/parity_spec.py`, `tests/test_customer_read_model_next_primary.py`, `tests/test_messages_exact_routes.py`, `docs/mcp_usage.md` | `aicrm_next.crm.customer_read_model` | `aicrm_next.crm.customer_read_model` | read | none | Already exact Next route; customer read model owns it and production errors return `production_unavailable` | customer read legacy deleted; no legacy fallback allowed | `tests/test_customer_read_model_next_primary.py`, `historical removed reference (test_messages_registry_lifecycle.py)` |
| `/api/messages/{external_userid}` | GET | `docs/crm_sensitive_routes.md`, `tests/test_messages_exact_routes.py` | deleted production_compat wildcard | `aicrm_next.extensions.archive.message_archive` | read | none | Exact Next message archive list route owns this surface | broad wildcard deleted; no legacy forward | `tests/test_messages_exact_routes.py` |
| `/api/messages/search` | GET | `docs/crm_sensitive_routes.md`, `tests/test_messages_exact_routes.py` | deleted production_compat wildcard | `aicrm_next.extensions.archive.message_archive` | read | none | Exact Next message archive search route owns this surface | broad wildcard deleted; no legacy forward | `tests/test_messages_exact_routes.py` |
| `/api/messages/archive` | GET | no active caller found; historical archive naming surface | deleted production_compat wildcard | `aicrm_next.extensions.archive.message_archive` | read | none | Explicit deprecated response with replacement route | locked deleted; no legacy forward | `tests/test_messages_exact_routes.py` |
| `/api/messages/{external_userid}/archive` | GET | no active caller found; historical archive naming surface | deleted production_compat wildcard | `aicrm_next.extensions.archive.message_archive` | read | none | Explicit deprecated response with replacement route | locked deleted; no legacy forward | `tests/test_messages_exact_routes.py` |
| `/api/messages/{external_userid}/history` | GET | no active caller found; historical history naming surface | deleted production_compat wildcard | `aicrm_next.extensions.archive.message_archive` | read | none | Explicit deprecated response with replacement route | locked deleted; no legacy forward | `tests/test_messages_exact_routes.py` |
| `/api/messages/send` | GET/POST/OPTIONS | no active caller found; historical write/send naming surface | deleted production_compat wildcard | `aicrm_next.extensions.archive.message_archive` | write | real blocked | Explicit blocked response with `side_effect_plan`; no real WeCom send | locked deleted; no legacy forward | `tests/test_messages_no_real_side_effects.py` |
| `/api/messages/broadcast` | GET/POST/OPTIONS | no active caller found; historical broadcast naming surface | deleted production_compat wildcard | `aicrm_next.extensions.archive.message_archive` | write | real blocked | Explicit blocked response with `side_effect_plan`; no real WeCom send | locked deleted; no legacy forward | `tests/test_messages_no_real_side_effects.py` |
| `/api/messages/archive/sync` | GET/POST/OPTIONS | no active caller found; archive sync uses `/api/archive/sync`, not this route | deleted production_compat wildcard | `aicrm_next.extensions.archive.message_archive` | write/sync | real blocked | Explicit blocked response with `side_effect_plan`; no real archive sync | locked deleted; no legacy forward | `tests/test_messages_no_real_side_effects.py` |
| `/api/messages*` | ALL | production_compat catch-all deleted | deleted production_compat wildcard | deleted/locked record only | mixed | guarded | Broad wildcard removed from production_compat; exact Next routes own known message surfaces | `legacy_deleted`; no legacy forward; no real external calls enabled | `historical removed reference (test_messages_registry_lifecycle.py)` |

Search evidence:

- `aicrm_next/crm/customer_read_model/api.py` owns `GET /api/messages/{external_userid}/recent`.
- `historical retired production_compat module` no longer contains `@wildcard_router.api_route("/api/messages/{path:path}")`.
- `aicrm_next/extensions/archive/message_archive/api.py` owns the new exact archive list/search routes plus explicit deprecated/blocked routes listed above.
- `aicrm_next/platform/admin_config/api_docs_view_model.py` only groups `/api/messages/` paths for API docs display.
- `tests/test_customer_read_model_next_primary.py` and `tests/test_messages_exact_routes.py` reference `GET /api/messages/{external_userid}`, `GET /api/messages/{external_userid}/recent`, and `GET /api/messages/search`.
- `tests/test_messages_*.py` are validation coverage for message archive business behavior.
- Docs references are limited to `docs/crm_sensitive_routes.md` and `docs/mcp_usage.md`.

Non-goals:

- No real WeCom send is enabled.
- No OpenClaw, payment, media storage, questionnaire, user-ops, or automation runtime send path is changed.
- `/api/messages*` broad wildcard is deleted and locked in this group.
- The deletion does not enable real WeCom, OpenClaw, payment, storage, media, questionnaire, user-ops, or automation calls.
