from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aicrm_next.identity_contact.wechat_unionid_guard import evaluate_wechat_unionid_access
from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.wechat_h5_session import (
    is_wechat_browser,
    payment_identity_from_request,
    payment_oauth_start_url,
)

from .application import CouponPublicApplication


router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=[_TEMPLATES_DIR])

_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


def _identity(request: Request) -> dict[str, str]:
    return payment_identity_from_request(request)


def _oauth_payload(public_slug: str) -> dict[str, Any]:
    return {
        "ok": False,
        "identity_ready": False,
        "error": "unionid_oauth_required",
        "message": "请先完成微信授权，获取稳定身份后继续。",
        "oauth_start_url": payment_oauth_start_url(f"/c/{quote(public_slug, safe='')}"),
    }


def _json(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(payload), status_code=status_code, headers=_HEADERS)


@router.get("/c/{public_slug}", response_class=HTMLResponse, name="api.public_coupon_page")
def public_coupon_page(request: Request, public_slug: str):
    identity = _identity(request)
    access = evaluate_wechat_unionid_access(
        identity,
        is_wechat_browser=is_wechat_browser(request),
        oauth_start_url=payment_oauth_start_url(f"/c/{quote(public_slug, safe='')}"),
    )
    try:
        state = CouponPublicApplication().get_coupon(
            public_slug,
            identity=identity if access.allowed else {},
        )
    except NotFoundError:
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><main data-route-owner='ai_crm_next'>优惠券不存在</main>",
            status_code=404,
            headers=_HEADERS,
        )
    if is_wechat_browser(request) and not access.allowed:
        return RedirectResponse(
            access.oauth_start_url,
            status_code=302,
            headers=_HEADERS,
        )
    context = {
        "request": request,
        "state": jsonable_encoder(state),
        "state_json": jsonable_encoder(state),
        "public_slug": public_slug,
        "is_wechat": is_wechat_browser(request),
        "identity_ready": access.allowed,
        "identity_message": access.message,
    }
    return templates.TemplateResponse(request, "coupon_public.html", context, headers=_HEADERS)


@router.get("/api/h5/coupons/available", name="api.h5_available_coupons")
def available_coupons(
    request: Request,
    target_ref: str = Query(..., min_length=1, max_length=200),
) -> JSONResponse:
    identity = _identity(request)
    return_url = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    access = evaluate_wechat_unionid_access(
        identity,
        is_wechat_browser=is_wechat_browser(request),
        oauth_start_url=payment_oauth_start_url(return_url),
    )
    if not access.allowed:
        return _json(access.payload(), status_code=access.status_code)
    try:
        payload = CouponPublicApplication().list_available_claims(target_ref, identity=identity)
    except ContractError as exc:
        return _json({"ok": False, "error": "invalid_target_ref", "detail": str(exc)}, status_code=400)
    return _json(payload)


@router.get("/api/h5/coupons/{public_slug}", name="api.h5_coupon_state")
def public_coupon_state(request: Request, public_slug: str) -> JSONResponse:
    identity = _identity(request)
    access = evaluate_wechat_unionid_access(
        identity,
        is_wechat_browser=is_wechat_browser(request),
        oauth_start_url=payment_oauth_start_url(f"/c/{quote(public_slug, safe='')}"),
    )
    if not access.allowed:
        return _json(access.payload(), status_code=access.status_code)
    try:
        payload = CouponPublicApplication().get_coupon(public_slug, identity=identity)
    except NotFoundError:
        return _json({"ok": False, "error": "coupon_not_found"}, status_code=404)
    return _json(payload)


@router.post("/api/h5/coupons/{public_slug}/claim", name="api.h5_coupon_claim")
def claim_public_coupon(request: Request, public_slug: str) -> JSONResponse:
    if not is_wechat_browser(request):
        return _json(
            {"ok": False, "identity_ready": False, "error": "wechat_browser_required", "message": "请在微信中打开后完成授权。"},
            status_code=403,
        )
    identity = _identity(request)
    access = evaluate_wechat_unionid_access(
        identity,
        is_wechat_browser=True,
        oauth_start_url=payment_oauth_start_url(f"/c/{quote(public_slug, safe='')}"),
    )
    if not access.allowed:
        return _json(_oauth_payload(public_slug), status_code=401)
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not idempotency_key:
        return _json({"ok": False, "error": "idempotency_key_required"}, status_code=400)
    try:
        payload = CouponPublicApplication().claim_coupon(
            public_slug,
            identity=identity,
            idempotency_key=idempotency_key,
        )
    except NotFoundError:
        return _json({"ok": False, "error": "coupon_not_found"}, status_code=404)
    except ContractError as exc:
        return _json({"ok": False, "error": "coupon_unavailable", "detail": str(exc)}, status_code=409)
    return _json(payload, status_code=201)


__all__ = ["router"]
