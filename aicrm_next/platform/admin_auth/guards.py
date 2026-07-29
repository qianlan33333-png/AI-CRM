from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from aicrm_next.platform.platform_foundation.auth_platform.api import auth_session_service
from aicrm_next.platform.platform_foundation.auth_platform.context import AuthContext
from aicrm_next.platform.platform_foundation.auth_platform.sessions import SessionIntrospection
from aicrm_next.platform.shared.runtime import production_data_ready, production_environment, test_environment
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting

from .service import SESSION_COOKIE, normalize_text, route_headers, safe_next_path


PROTECTED_ROUTE_PREFIXES = (
    "/admin",
    "/setup",
    "/api/admin",
    "/api/customers",
    "/api/users",
    "/api/messages",
    "/archive/messages",
)

PUBLIC_ROUTE_PREFIXES = (
    "/auth/wecom/",
    "/oauth/",
    "/.well-known/",
    "/api/h5/",
    "/api/wecom/events",
    "/wecom/external-contact/callback",
    "/static/",
)

PUBLIC_EXACT_ROUTES = {
    "/health",
    "/api/system/health",
    "/login",
    "/logout",
    "/api/sidebar/jssdk-config",
    "/sidebar/bind-mobile",
}

ADMIN_PAGE_ROUTE_PREFIXES = ("/admin", "/setup")


def current_admin_introspection(request: Request) -> SessionIntrospection:
    cached = getattr(request.state, "auth_session_introspection", None)
    if isinstance(cached, SessionIntrospection):
        return cached
    session_cookie = normalize_text(request.cookies.get(SESSION_COOKIE, ""))
    if not session_cookie:
        result = SessionIntrospection(active=False, error="session_required")
    else:
        result = auth_session_service(request).introspect(session_cookie)
    request.state.auth_session_introspection = result
    if result.active and result.context is not None:
        request.state.auth_context = result.context
    return result


def current_auth_context(request: Request) -> AuthContext | None:
    cached = getattr(request.state, "auth_context", None)
    if isinstance(cached, AuthContext):
        return cached
    return current_admin_introspection(request).context


def admin_auth_enforcement_enabled() -> bool:
    value = normalize_text(managed_runtime_setting("AICRM_ADMIN_AUTH_ENFORCED")).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return not _admin_auth_disable_override_allowed()
    return _production_admin_auth_required()


def _production_admin_auth_required() -> bool:
    return production_environment() or production_data_ready()


def _admin_auth_disable_override_allowed() -> bool:
    if test_environment():
        return True
    return not _production_admin_auth_required()


def is_protected_admin_path(path: str) -> bool:
    normalized = normalize_text(path) or "/"
    if normalized in PUBLIC_EXACT_ROUTES:
        return False
    if normalized.startswith(PUBLIC_ROUTE_PREFIXES):
        return False
    return normalized.startswith(PROTECTED_ROUTE_PREFIXES)


def admin_auth_required_response(request: Request) -> Response | None:
    if not admin_auth_enforcement_enabled() or not is_protected_admin_path(str(request.url.path or "/")):
        return None
    if current_auth_context(request) is not None:
        return None
    if str(request.url.path or "").startswith(ADMIN_PAGE_ROUTE_PREFIXES):
        return admin_page_auth_redirect(request)
    return admin_api_auth_error(request)


def require_admin(request: Request) -> AuthContext:
    context = current_auth_context(request)
    if context is None:
        raise HTTPException(status_code=401, detail="admin_auth_required")
    return context


def admin_api_auth_error(request: Request) -> JSONResponse | None:
    if not admin_auth_enforcement_enabled():
        return None
    if current_auth_context(request) is not None:
        return None
    return JSONResponse(
        {
            "ok": False,
            "error": "admin_auth_required",
            "route_owner": "ai_crm_next",
            "real_external_call_executed": False,
        },
        status_code=401,
        headers=route_headers(),
    )


def admin_page_auth_redirect(request: Request) -> RedirectResponse | None:
    if not admin_auth_enforcement_enabled():
        return None
    if current_auth_context(request) is not None:
        return None
    next_path = safe_next_path(str(request.url.path or "/admin"))
    if request.url.query:
        next_path = safe_next_path(f"{next_path}?{request.url.query}")
    return RedirectResponse(
        f"/login?next={quote(next_path, safe='/?:=&')}",
        status_code=302,
        headers=route_headers(),
    )
