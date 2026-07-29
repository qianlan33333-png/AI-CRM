# Sidebar JSSDK Route Inventory

Scope: Legacy Exit group 15 closeout locks `/api/sidebar/jssdk-config` to the Next JSSDK adapter and removes legacy rollback. The route supports explicitly gated real WeCom JSSDK signing for sidebar identity resolution, but does not enable material send, tag mutation, payment, storage, OpenClaw, or automation runtime.

## Frontend ↔ API ↔ Backend Contract Matrix

| 页面/前端 | API | Backend | Contract | Smoke |
| --- | --- | --- | --- | --- |
| `/sidebar/bind-mobile` | 页面入口 | `aicrm_next.frontend_compat` route renders `sidebar_customer_workbench.html` | 页面非空，含 V2 workbench，含 `data-jssdk-config-url="/api/sidebar/jssdk-config"` | `curl` page smoke 200 |
| `sidebar_customer_workbench.html` | `data-jssdk-config-url` | template `aicrm_next/frontend_compat/templates/sidebar_customer_workbench.html` | 指向 `/api/sidebar/jssdk-config` | `grep` + smoke |
| `sidebar_workbench.js` | `GET /api/sidebar/jssdk-config?url=...`; optional redirect to `/api/sidebar/oauth/start` | JS fetch in `aicrm_next/frontend_compat/static/sidebar_workbench/sidebar_workbench.js` | 读取 `corp_id` / `agent_id` / `config` / `agent_config` with `timestamp`, `nonceStr`, `signature`, `jsApiList`; when current viewer userid is still missing for a multi-owner customer, starts sidebar OAuth once and returns to the same customer URL | `grep` |
| JSSDK API | `GET` / `HEAD` / `OPTIONS` `/api/sidebar/jssdk-config` | Next adapter `aicrm_next.identity_contact.sidebar_jssdk` + `aicrm_next.channels.integration_gateway.wecom_jssdk_adapter` | `route_owner=ai_crm_next`, `fallback_used=false`, no `X-AICRM-Compatibility-Facade`; default `real_blocked` returns a shaped non-real contract with `real_external_call_executed=false`; explicit `real_enabled` fetches WeCom token/tickets and returns real `config` / `agent_config` with `real_external_call_executed=true`; GET returns `ok`, `appId`, `corpId`, `corp_id`, `agentId`, `agent_id`, `timestamp`, `nonceStr`, `signature`, `jsApiList`, `source_status`, `adapter_mode`, `config`, `agent_config`; HEAD returns 204; OPTIONS returns allowed methods | `curl` API smoke |
| Sidebar viewer OAuth | `GET` / `OPTIONS` `/api/sidebar/oauth/start`; `GET` / `OPTIONS` `/api/sidebar/oauth/callback` | Next adapter `aicrm_next.identity_contact.sidebar_jssdk` + `aicrm_next.channels.integration_gateway.wecom_admin_auth_client` | OAuth is only a current employee identity fallback. It does not grant admin session. Callback stores an independent `aicrm_sidebar_viewer_session` cookie only after resolving WeCom `UserId`; owner token signing still rejects viewers outside the current external contact owner candidates. | `pytest` adapter contract |

GET accepts `url`, optional `debug`, optional `agentid` / `agent_id` / `agentId`, and optional `corp_id` / `corpId` / `corpid`.

## Adapter Modes

| Mode | Default | Behavior |
| --- | --- | --- |
| `fake` | local/test default | Returns a deterministic signing contract for frontend initialization tests; no external call. |
| `sandbox` | explicit `AICRM_SIDEBAR_JSSDK_ADAPTER_MODE=sandbox` | Returns the same contract shape for sandbox checks; no external call. |
| `real_blocked` | production default | Returns a blocked-but-shaped contract with `external_call_blocked=true`; no external call. |
| `real_enabled` | requires explicit `AICRM_SIDEBAR_JSSDK_ADAPTER_MODE=real_enabled` and `AICRM_SIDEBAR_JSSDK_REAL_ENABLED=1` | Fetches `access_token`, corp `jsapi_ticket`, and agent-config ticket from WeCom, then returns signatures needed by `wx.config` and `wx.agentConfig`. |

## Boundaries

1. The Next route is registered before `production compatibility router`, so page/API smoke must not hit `X-AICRM-Compatibility-Facade`.
2. The legacy production_compat exact route has been removed; `/api/sidebar/jssdk-config` is Next adapter only and `legacy_fallback_allowed=false`.
3. Real WeCom signing is allowed only for this JSSDK route and only under the explicit real-enabled gate.
4. Sidebar viewer OAuth is separately gated by `AICRM_SIDEBAR_WECOM_OAUTH_ENABLE_REAL` or existing admin WeCom auth enablement, and it writes only the side-specific viewer cookie.
5. Material send, tag mutation, payment, storage, OpenClaw, and automation runtime remain out of scope.
6. Default fake/sandbox/real_blocked responses record AuditLedger planned/blocked attempts with `real_external_call_executed=false`; explicit real_enabled records the real signing attempt.
