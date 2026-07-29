from __future__ import annotations

from typing import Any
from urllib.parse import quote

from aicrm_next.admin_shell_contract import admin_path_for
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository
from aicrm_next.platform.shared.runtime_settings import managed_runtime_bool, managed_runtime_setting


SESSION_COOKIE = "aicrm_next_admin_session"
CSRF_COOKIE = "aicrm_next_csrf"
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
DEFAULT_NEXT_PATH = "/admin"


def reset_admin_auth_fixture_state() -> None:
    return None


def route_headers() -> dict[str, str]:
    return {
        "X-AICRM-Route-Owner": "ai_crm_next",
        "X-AICRM-Fallback-Used": "false",
        "X-AICRM-Real-External-Call-Executed": "false",
        "X-AICRM-WeCom-Token-Exchange-Executed": "false",
    }


def diagnostics_payload(route: str) -> dict[str, Any]:
    return {
        "ok": True,
        "route": route,
        "route_owner": "ai_crm_next",
        "source_status": "next_admin_auth",
        "fallback_used": False,
        "real_external_call_executed": False,
        "wecom_token_exchange_executed": False,
        "allowed_methods": ["GET", "OPTIONS"],
    }


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def safe_next_path(value: Any, *, default: str = DEFAULT_NEXT_PATH) -> str:
    raw = normalize_text(value)
    if not raw:
        return default
    if raw.startswith("//") or "://" in raw or raw.startswith("\\"):
        return default
    if not raw.startswith("/"):
        return default
    if raw.startswith(("/static", "/api/", "/auth/wecom/callback")):
        return default
    return raw


def wecom_login_links(next_path: str) -> dict[str, str]:
    encoded = quote(safe_next_path(next_path), safe="/")
    return {
        "qr": f"/auth/wecom/start?mode=qr&next={encoded}",
        "oauth": f"/auth/wecom/start?mode=oauth&next={encoded}",
    }


def admin_user_count() -> int:
    return PostgresAuthRepository().count_admin_users()


def login_context(*, request: Any, next_path: Any = "", page_error: str = "", page_notice: str = "") -> dict[str, Any]:
    safe_next = safe_next_path(next_path)
    return {
        "request": request,
        "page_title": "后台登录",
        "page_notice": page_notice,
        "page_error": page_error,
        "next_path": safe_next,
        "login_links": wecom_login_links(safe_next),
        "wecom_auth_mode": "next_safe_mode",
        "wecom_corp_id": normalize_text(managed_runtime_setting("WECOM_CORP_ID")),
        "wecom_agent_id": normalize_text(managed_runtime_setting("WECOM_AGENT_ID")),
        "admin_user_count": admin_user_count(),
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
        "url_for": _login_url_for,
    }


def login_error_message(error_code: Any) -> str:
    code = normalize_text(error_code)
    messages = {
        "wecom_admin_auth_not_enabled": "企业微信扫码登录还未启用真实授权，请完成企微登录配置。",
        "wecom_admin_auth_config_missing": "企业微信登录配置不完整，请检查企业 ID、应用 ID、应用 Secret 与回调地址。",
        "invalid_state": "登录状态已过期，请重新发起企业微信登录。",
        "missing_code": "企业微信没有返回授权 code，请重新扫码。",
        "wecom_" + "access_" + "token_failed": "企业微信接口凭证获取失败，请检查应用 Secret。",
        "wecom_" + "access_" + "token_missing": "企业微信接口凭证返回为空，请检查应用配置。",
        "wecom_userinfo_failed": "企业微信用户身份获取失败，请重新扫码。",
        "wecom_userid_missing": "企业微信没有返回成员 UserId，当前账号无法进入后台。",
        "admin_user_not_authorized": "当前企微成员尚未被授权进入后台，请先在“后台访问”中添加该成员。",
        "admin_user_disabled": "当前后台成员已停用，无法登录。",
        "wecom_admin_auth_http_error": "企业微信登录请求失败，请稍后重试。",
        "wecom_admin_auth_response_invalid": "企业微信登录响应异常，请稍后重试。",
    }
    return messages.get(code, "")


def _login_url_for(name: str, **path_params: object) -> str:
    if name == "api.admin_login":
        return "/login"
    if name == "api.admin_logout":
        return "/logout"
    return admin_path_for(name, **path_params)


def admin_cookie_secure() -> bool:
    from aicrm_next.platform.shared.runtime import secure_cookie_environment

    if secure_cookie_environment():
        return True
    return managed_runtime_bool("AICRM_ADMIN_SESSION_COOKIE_SECURE", False)
