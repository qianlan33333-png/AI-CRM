from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse

from aicrm_next.admin_auth.browser_sessions import issue_browser_session, set_browser_session_cookies

from .service import (
    auth_route_headers,
    auth_safe_next_path,
    build_authorize_url,
    handle_callback,
)

router = APIRouter()


def _response(payload: dict) -> JSONResponse:
    status_code = int(payload.pop("status_code", 200) or 200)
    return JSONResponse(jsonable_encoder(payload), status_code=status_code)


def _request_base_url(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept.lower()


def _login_redirect(next_path: str, error_code: str) -> RedirectResponse:
    safe_next = auth_safe_next_path(next_path)
    return RedirectResponse(
        f"/login?{urlencode({'next': safe_next, 'auth_error': error_code})}",
        status_code=302,
        headers=auth_route_headers(),
    )


def _options_payload(route: str) -> dict:
    return {
        "ok": True,
        "route": route,
        "route_owner": "ai_crm_next",
        "source_status": "next_auth_wecom_exact",
        "adapter_mode": "real_blocked",
        "fallback_used": False,
        "real_external_call_executed": False,
        "status_code": 200,
    }


def _blocked_auth_payload(*, auth_step: str, would_call: str, replacement_route: str = "") -> dict:
    return {
        "ok": False,
        "error": "external_call_blocked",
        "error_code": "external_call_blocked",
        "message": "WeCom admin auth external calls are blocked by default in AI-CRM Next.",
        "auth_step": auth_step,
        "replacement_route": replacement_route,
        "route_owner": "ai_crm_next",
        "source_status": "external_call_blocked",
        "adapter_mode": "real_blocked",
        "fallback_used": False,
        "real_external_call_executed": False,
        "side_effect_plan": {
            "would_call": would_call,
            "adapter_mode": "real_blocked",
            "real_external_call_executed": False,
            "next_step": "auth_wecom_wildcard_validation",
        },
        "status_code": 503,
    }


def _deprecated_payload(*, route: str, replacement_route: str = "") -> dict:
    return {
        "ok": False,
        "error": "auth_route_deprecated",
        "error_code": "auth_route_deprecated",
        "message": "This historical auth route is deprecated and now has an explicit Next response.",
        "route": route,
        "replacement_route": replacement_route,
        "route_owner": "ai_crm_next",
        "source_status": "deprecated",
        "adapter_mode": "real_blocked",
        "fallback_used": False,
        "real_external_call_executed": False,
        "status_code": 410,
    }


@router.api_route("/auth/wecom/start", methods=["GET", "OPTIONS"])
def auth_wecom_start(request: Request):
    if request.method == "OPTIONS":
        return _response(_options_payload("/auth/wecom/start"))
    next_path = auth_safe_next_path(request.query_params.get("next"))
    start = build_authorize_url(
        mode=str(request.query_params.get("mode") or "qr"),
        next_path=next_path,
        request_base_url=_request_base_url(request),
    )
    if start.ok and start.redirect_url:
        return RedirectResponse(start.redirect_url, status_code=302, headers=auth_route_headers())
    if _wants_html(request):
        return _login_redirect(next_path, start.error_code or "wecom_admin_auth_not_enabled")
    return _response(
        _blocked_auth_payload(
            auth_step="wecom_sso_start",
            would_call="wecom_qr_or_oauth_authorize_url",
            replacement_route="/login",
        )
    )


@router.api_route("/auth/wecom/callback", methods=["GET", "OPTIONS"])
def auth_wecom_callback(request: Request):
    if request.method == "OPTIONS":
        return _response(_options_payload("/auth/wecom/callback"))
    result = handle_callback(
        code=str(request.query_params.get("code") or ""),
        state=str(request.query_params.get("state") or ""),
        request_base_url=_request_base_url(request),
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )
    if result.ok and result.identity_claims:
        issued = issue_browser_session(request, result.identity_claims)
        response = RedirectResponse(result.next_path, status_code=302, headers=auth_route_headers())
        set_browser_session_cookies(response, issued)
        return response
    if _wants_html(request):
        return _login_redirect(result.next_path, result.error_code or "wecom_admin_auth_failed")
    return _response(
        _blocked_auth_payload(
            auth_step="wecom_sso_callback",
            would_call="wecom_sso_code_exchange",
            replacement_route="/login",
        )
    )


@router.options("/auth/wecom/unknown")
def auth_wecom_unknown_options():
    return _response(_options_payload("/auth/wecom/unknown"))


@router.get("/auth/wecom/unknown")
def auth_wecom_unknown():
    return _response(_deprecated_payload(route="/auth/wecom/unknown", replacement_route="/auth/wecom/start"))


@router.options("/api/h5/wechat/oauth/unknown")
def h5_wechat_oauth_unknown_options():
    return _response(_options_payload("/api/h5/wechat/oauth/unknown"))


@router.get("/api/h5/wechat/oauth/unknown")
def h5_wechat_oauth_unknown():
    return _response(_deprecated_payload(route="/api/h5/wechat/oauth/unknown", replacement_route="/api/h5/wechat/oauth/start"))
