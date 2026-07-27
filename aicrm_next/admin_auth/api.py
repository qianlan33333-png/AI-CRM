from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .browser_sessions import revoke_browser_session
from .guards import current_auth_context
from .service import (
    diagnostics_payload,
    login_context,
    login_error_message,
    route_headers,
    safe_next_path,
)


router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "admin_console" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.options("/login", name="api.admin_login_options")
def admin_login_options() -> JSONResponse:
    return JSONResponse(diagnostics_payload("/login"), headers=route_headers())


@router.get("/login", name="api.admin_login")
def admin_login_page(request: Request):
    next_path = safe_next_path(request.query_params.get("next"))
    if current_auth_context(request) is not None:
        return RedirectResponse(next_path, status_code=302, headers=route_headers())
    context = login_context(
        request=request,
        next_path=next_path,
        page_error=login_error_message(request.query_params.get("auth_error")),
    )
    return templates.TemplateResponse(request, "admin_console/login.html", context, headers=route_headers())


@router.options("/logout", name="api.admin_logout_options")
def admin_logout_options() -> JSONResponse:
    return JSONResponse(diagnostics_payload("/logout"), headers=route_headers())


@router.get("/logout", name="api.admin_logout")
def admin_logout(request: Request) -> RedirectResponse:
    revoke_browser_session(request)
    response = RedirectResponse("/login", status_code=302, headers=route_headers())
    from .service import CSRF_COOKIE, SESSION_COOKIE

    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response
