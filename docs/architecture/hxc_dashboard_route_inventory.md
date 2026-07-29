# HXC Dashboard Route Inventory

## Scope

HXC dashboard closeout replaces the production `production_compat` fallback for `/admin/hxc-dashboard`, `/admin/hxc-send-config`, and `/api/admin/hxc-dashboard*` with Next-owned routes.

The old Flask module remains only as historical reference. Runtime traffic for this route family must not call `forward_to_legacy_flask`, `refresh_hxc_dashboard_snapshot`, `sync_admin_wecom_directory_members`, or `broadcast_to_filtered_users`.

## Frontend <-> API <-> Backend Contract Matrix

| Surface | Method | Next owner | Frontend caller | Backend command/read model | Side effect policy | Legacy fallback |
| --- | --- | --- | --- | --- | --- | --- |
| `/admin/hxc-dashboard` | GET | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Admin navigation and `/admin/user-ops` redirect | `dashboard_payload()` safe-mode read model | No external calls | `legacy_fallback_allowed=false`, `deletion_locked` |
| `/admin/hxc-send-config` | GET | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Dashboard "发送人管理" link | `send_config_payload()` safe-mode read model | No external calls | `legacy_fallback_allowed=false`, `deletion_locked` |
| `/api/admin/hxc-dashboard` | GET | `aicrm_next.extensions.hxc.hxc_dashboard.api` | API readers and smoke checks | `dashboard_payload()` | `real_external_call_executed=false` | `legacy_fallback_allowed=false`, `deletion_locked` |
| `/api/admin/hxc-dashboard/refresh` | POST/OPTIONS | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Manual refresh button | `PlanHxcDashboardRefreshCommand` | Creates blocked `SideEffectPlan` and blocked `ExternalCallAttempt`; `hxc_refresh_executed=false` | `legacy_fallback_allowed=false`, `deletion_locked` |
| `/api/admin/hxc-dashboard/refresh-directory` | POST/OPTIONS | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Send-config page sync button | `PlanHxcDirectorySyncCommand` | Creates blocked `SideEffectPlan` and blocked `ExternalCallAttempt`; `directory_sync_executed=false`, `wecom_api_called=false` | `legacy_fallback_allowed=false`, `deletion_locked` |
| `/api/admin/hxc-dashboard/send-config` | GET | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Send-config page load | `send_config_payload()` | Local safe-mode data only | `legacy_fallback_allowed=false`, `deletion_locked` |
| `/api/admin/hxc-dashboard/send-config` | POST/OPTIONS | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Send-config save form | `UpsertHxcSendConfigCommand` | Local safe-mode config mutation only | `legacy_fallback_allowed=false`, `deletion_locked` |
| `/api/admin/hxc-dashboard/send-config/{sender_userid}` | DELETE/OPTIONS | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Send-config delete button | `DeleteHxcSendConfigCommand` | Local safe-mode config mutation only | `legacy_fallback_allowed=false`, `deletion_locked` |
| `/api/admin/hxc-dashboard/broadcast` | POST/OPTIONS | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Legacy callers only; dashboard UI uses `/broadcast-tasks` | `PlanHxcBroadcastCommand` | Creates blocked `SideEffectPlan` and blocked `ExternalCallAttempt`; `hxc_broadcast_executed=false`, `wecom_send_executed=false` | `legacy_fallback_allowed=false`, `deletion_locked` |
| `/api/admin/hxc-dashboard/broadcast-tasks` | POST | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Dashboard composer | Existing Next-native broadcast task creation | Creates internal task/preview only; no deleted fallback path | Existing Next route remains unchanged |
| `/api/admin/hxc-dashboard/{unknown_path}` | GET/POST/OPTIONS | `aicrm_next.extensions.hxc.hxc_dashboard.api` | Unknown clients | Controlled 404 payload | No external calls | `legacy_fallback_allowed=false`, `deletion_locked` |

## Current Next Ownership

- `/admin/hxc-dashboard`, `/admin/hxc-send-config`, and `/api/admin/hxc-dashboard*` are served by `aicrm_next.extensions.hxc.hxc_dashboard.api`.
- Existing HXC dashboard business tests cover page rendering, dashboard payloads, safe-mode refresh/directory sync/broadcast planning, and config mutations.

## Explicit Non-Goals

- No real HXC dashboard refresh.
- No real WeCom directory sync.
- No real HXC broadcast.
- No direct HTTP client, WeCom client, OpenClaw client, or access-token path in the Next HXC closeout routes.
- Login/logout, payment/product pages, and other production_compat surfaces are out of this closeout scope.
