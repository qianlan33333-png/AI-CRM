# Sidebar Readonly Route Inventory

Status: Legacy Exit group 4 Next-native readonly replacement,专项测试通过, and closeout locked. R04 further seals every listed route behind the customer-bound OAuth viewer context defined in `sidebar_questionnaire_access_boundary.md`; the former guarded v2 fallback is removed.

Scope: Sidebar readonly routes only. Sidebar write paths, JSSDK signing, and material send are inventoried as out of scope for this group and keep their existing guarded fallback behavior.

Closeout lock: the readonly routes below are sealed as `deletion_locked`; no production_compat fallback, no legacy sidebar read facade, no compatibility facade header, and no real WeCom/OpenClaw/payment/storage calls are allowed for these readonly paths.

Acceptance marker: readonly sealed; no legacy fallback.

| Path | Method | Read/write | Current owner | Expected owner | Data source | External side effect risk | Replacement decision | Delete decision | Test coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/api/sidebar/customer-context` | GET | read | `aicrm_next.crm.customer_read_model` | `aicrm_next.crm.customer_read_model` | customer read model, recent messages projection, timeline projection | none | Keep exact Next route; closeout lock stable diagnostics | readonly sealed; deletion_locked; no legacy fallback | `tests/test_sidebar_readonly_next_native.py` |
| `/api/sidebar/profile` | GET | read | not registered before group 4 | `aicrm_next.crm.customer_read_model` | customer read model profile query | none | Add exact Next route alias for sidebar profile reads; closeout locked | readonly sealed; deletion_locked; no legacy fallback | `tests/test_sidebar_readonly_next_native.py` |
| `/api/sidebar/tags` | GET | read | not registered before group 4 | `aicrm_next.crm.customer_read_model` | customer read model tag projection | none | Add exact Next route alias for sidebar tag reads; closeout locked | readonly sealed; deletion_locked; no legacy fallback | `tests/test_sidebar_readonly_next_native.py` |
| `/api/sidebar/binding-status` | GET | read | not registered before group 4 | `aicrm_next.crm.identity_contact` | identity/contact binding read model, customer read model in production | none | Add exact Next route alias for binding reads; closeout locked | readonly sealed; deletion_locked; no legacy fallback | `tests/test_sidebar_readonly_next_native.py` |
| `/api/sidebar/contact-binding-status` | GET | read | `aicrm_next.crm.identity_contact` | `aicrm_next.crm.identity_contact` | identity/contact binding read model, customer read model in production | none | Keep exact Next route; closeout lock stable diagnostics | readonly sealed; deletion_locked; no legacy fallback | `historical removed reference (test_sidebar_readonly_registry_lifecycle.py)` |
| `/api/sidebar/lead-pool/status` | GET | read | `aicrm_next.crm.customer_read_model` route with legacy sidebar facade source | `aicrm_next.crm.customer_read_model` | customer read model class status and sidebar context projection | none | Replace legacy read facade with Next read model derived status; closeout locked | readonly sealed; deletion_locked; no legacy fallback | `tests/test_sidebar_readonly_next_native.py` |
| `/api/sidebar/signup-tags/status` | GET | read | `aicrm_next.crm.customer_read_model` route with legacy sidebar facade source | `aicrm_next.crm.customer_read_model` | customer read model class status, tags, marketing profile | none | Replace legacy read facade with Next read model derived status; closeout locked | readonly sealed; deletion_locked; no legacy fallback | `tests/test_sidebar_readonly_next_native.py` |
| `/api/sidebar/marketing-status` | GET | read | `aicrm_next.crm.customer_read_model` route with legacy sidebar facade source | `aicrm_next.crm.customer_read_model` | customer read model marketing summary/profile and class status | none | Replace legacy read facade with Next read model derived status; closeout locked | readonly sealed; deletion_locked; no legacy fallback | `tests/test_sidebar_readonly_next_native.py` |
| `/api/sidebar/v2*` | GET | read | `aicrm_next.crm.customer_read_model` | `aicrm_next.crm.customer_read_model` | Next read models | none | R04 requires signed OAuth viewer + customer + current owner scope | readonly fallback deleted; fail closed | `tests/test_sidebar_v2_api.py`, `tests/test_sidebar_owner_context_security.py` |
| `/api/sidebar/jssdk-config` | GET | adapter read | `aicrm_next.frontend_compat` / production compatibility | `aicrm_next.frontend_compat` | JSSDK signature adapter | guarded | Out of scope because it needs external signing semantics | keep existing behavior for later adapter group | existing production route tests |
| `/api/sidebar/bind-mobile` | POST | write | production compatibility | later sidebar write owner | legacy write path | guarded | Out of scope | do not delete in this group | `historical removed reference (test_sidebar_readonly_registry_lifecycle.py)` |
| `/api/sidebar/lead-pool/upsert-class-term` | POST | write | production compatibility | later sidebar write owner | legacy write path | guarded | Out of scope | do not delete in this group | `historical removed reference (test_sidebar_readonly_registry_lifecycle.py)` |
| `/api/sidebar/signup-tags/mark` | POST | write | production compatibility | later sidebar write owner | legacy write path | guarded | Out of scope | do not delete in this group | `historical removed reference (test_sidebar_readonly_registry_lifecycle.py)` |
| `/api/sidebar/marketing-status/*` | POST | write | production compatibility | later sidebar write owner | legacy write path | guarded | Out of scope | do not delete in this group | `historical removed reference (test_sidebar_readonly_registry_lifecycle.py)` |
| `/api/sidebar/v2/profile` | PUT | write | production compatibility | later sidebar write owner | legacy write path | guarded | Out of scope | do not delete in this group | `historical removed reference (test_sidebar_readonly_registry_lifecycle.py)` |
| `/api/sidebar/v2/materials/send` | POST | write | production compatibility | later sidebar write owner | legacy write path | guarded | Out of scope | do not delete in this group | `historical removed reference (test_sidebar_readonly_registry_lifecycle.py)` |

Search evidence:

- `aicrm_next/crm/customer_read_model/api.py` owns customer context, profile, tags, lead-pool status, signup-tag status, and marketing status readonly routes.
- `aicrm_next/crm/identity_contact/api.py` owns contact-binding-status and binding-status readonly routes.
- Sidebar readonly business tests cover customer context, profile, tags, binding status, lead-pool status, signup-tag status, and marketing status read paths.
- JSSDK signing, sidebar write paths, v2 profile writes, and material send writes remain separate business capability areas.

Non-goals:

- No sidebar write route is replaced or deleted in this group.
- No JSSDK signing path is sealed in this group.
- No sidebar material send route is replaced or deleted in this group.
- No real WeCom, OpenClaw, payment, storage, media, questionnaire, user-ops, or automation external call is enabled.
