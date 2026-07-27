# Admin Auth Login Route Inventory

## Scope

Legacy Exit group 26 replaces `/login` and `/logout` with Next-owned admin auth routes and removes their `production_compat` rollback.

`/auth/wecom/start` and `/auth/wecom/callback` are Next-owned. They remain
blocked by default, but can run the Next-native WeCom SSO flow only when the
explicit `AICRM_WECOM_ADMIN_AUTH_ENABLE_REAL=true` operator gate and required
WeCom configuration are present.

## Frontend <-> API <-> Backend Contract Matrix

| Surface | Method | Frontend/template | Action | Handler | Backend/Auth Service | External side effect | Closeout status | Smoke |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/login` | GET | `aicrm_next/app/admin_console/templates/admin_console/login.html` | Render Next login page with WeCom SSO links and break-glass slot | `aicrm_next.admin_auth.api.admin_login_page` | `login_context`, `safe_next_path`, signed-cookie session check | None; WeCom links point to `/auth/wecom/start` only | `legacy_fallback_allowed=false`, `deletion_locked`, `replacement_status=locked` | GET 200, non-empty, no compatibility facade |
| `/login` | POST | `aicrm_next/app/admin_console/templates/admin_console/login.html` form `action="/login"` | Break-glass submit | `aicrm_next.admin_auth.api.admin_login_submit` | `authenticate_break_glass`, `sign_session` | None; password checked locally, no WeCom token exchange | `legacy_fallback_allowed=false`, `deletion_locked`, `replacement_status=locked` | Invalid credential controlled 401 page; success 302 safe next |
| `/login` | OPTIONS | API diagnostics | Preflight/diagnostics | `aicrm_next.admin_auth.api.admin_login_options` | `diagnostics_payload` | None | `legacy_fallback_allowed=false`, `deletion_locked`, `replacement_status=locked` | OPTIONS 200 Next JSON |
| `/logout` | GET | Admin/user initiated | Clear Next admin auth cookie and redirect | `aicrm_next.admin_auth.api.admin_logout` | `SESSION_COOKIE` deletion | None | `legacy_fallback_allowed=false`, `deletion_locked`, `replacement_status=locked` | 302 `/login`, cookie cleared |
| `/logout` | OPTIONS | API diagnostics | Preflight/diagnostics | `aicrm_next.admin_auth.api.admin_logout_options` | `diagnostics_payload` | None | `legacy_fallback_allowed=false`, `deletion_locked`, `replacement_status=locked` | OPTIONS 200 Next JSON |
| `/auth/wecom/start` link | GET/OPTIONS | Login page links | SSO start link; browser blocked states return to `/login` with an auth error | `aicrm_next.channels.auth_wecom.api.auth_wecom_start` | `auth_wecom.service.build_authorize_url` | Default blocked; gated real redirect to WeCom QR/OAuth authorize URL only with `AICRM_WECOM_ADMIN_AUTH_ENABLE_REAL=true` | Next-owned gated route | JSON GET without HTML accept remains controlled blocked; HTML blocked state redirects to login error |
| `/auth/wecom/callback` | GET/OPTIONS | WeCom SSO return path | SSO callback signs Next admin session for authorized `admin_users` members | `aicrm_next.channels.auth_wecom.api.auth_wecom_callback` | `auth_wecom.service.handle_callback`, `admin_users`, `admin_user_roles`, `admin_login_audit` | Default blocked; gated real `gettoken` and `user/getuserinfo` only with `AICRM_WECOM_ADMIN_AUTH_ENABLE_REAL=true` | Next-owned gated route | Existing blocked tests plus gated fake-client callback test |

## Behavior Notes

- Already logged-in requests to `GET /login` redirect to a safe local `next` path or `/admin`.
- Unsafe `next` values such as `https://evil.example.com` are normalized to `/admin`.
- Missing or invalid break-glass credentials return a controlled login page error and do not 500.
- Break-glass success sets only the Next signed `aicrm_next_admin_session` cookie.
- No password is printed or echoed.
- `real_external_call_executed=false` and `wecom_token_exchange_executed=false` are exposed on route headers/diagnostics.

## Current Next Ownership

- `/login` and `/logout` are served by `aicrm_next.admin_auth.api`.
- Existing admin auth business tests cover page rendering, break-glass login, logout, safe redirect handling, and WeCom SSO blocked defaults.

## Explicit Non-Goals

- Do not enable real WeCom OAuth, token exchange, or access-token fetch by default.
- Do not enable the gated WeCom SSO flow without the explicit operator env gate
  and required WeCom configuration.
- Do not change `/api/h5/wechat/oauth/*`.
- Do not change payment, checkout, order, product, `/p/*`, or `/pay/*` fallbacks.
