from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.admin_action_runtime import validate_admin_action_token
from aicrm_next.shared.public_url import canonical_public_base_url

from .application import CouponAdminApplication
from .dto import CouponUpsertRequest


router = APIRouter()

_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


def _response(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(payload), status_code=status_code, headers=_HEADERS)


def _admin_actor_id(request: Request) -> str:
    context = getattr(request.state, "auth_context", None)
    for field in ("admin_user_id", "principal_id", "userid"):
        if isinstance(context, Mapping):
            value = context.get(field)
        else:
            value = getattr(context, field, None)
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return "admin_console"


def _write_application(request: Request) -> CouponAdminApplication:
    return CouponAdminApplication(actor_id=_admin_actor_id(request))


def _application_result(operation):
    try:
        return operation()
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _unsafe_action_error(request: Request) -> JSONResponse | None:
    # Pytest fixture routes intentionally disable the central policy unless a
    # security test opts in.  Production always validates the route-bound
    # token issued by Admin Shell and attached by AdminApi.
    if os.getenv("PYTEST_CURRENT_TEST") and str(
        os.getenv("AICRM_ROUTE_POLICY_ENFORCED")
        or os.getenv("AICRM_ADMIN_AUTH_ENFORCED")
        or ""
    ).strip().lower() in {"", "0", "false", "no", "off"}:
        return None
    token = str(request.headers.get("X-Admin-Action-Token") or "").strip()
    error = validate_admin_action_token(token, request=request)
    if not error:
        return None
    return _response({"ok": False, "error": "admin_action_token_invalid", "detail": error}, status_code=401)


@router.get("/api/admin/coupons", name="api.admin_coupons")
def list_coupons(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=80),
    status: str = Query("", max_length=32),
) -> JSONResponse:
    payload = _application_result(
        lambda: CouponAdminApplication().list_coupons(limit=limit, offset=offset, q=q, status=status)
    )
    return _response(payload)


@router.post("/api/admin/coupons", name="api.admin_coupon_create")
def create_coupon(request: Request, payload: CouponUpsertRequest) -> JSONResponse:
    if token_error := _unsafe_action_error(request):
        return token_error
    return _response(_application_result(lambda: _write_application(request).create_coupon(payload)), status_code=201)


@router.get("/api/admin/coupons/product-options", name="api.admin_coupon_product_options")
def list_coupon_product_options(
    request: Request,
    q: str = Query("", max_length=80),
    product_type: str = Query("all", pattern="^(all|standard_product|service_period)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    return _response(
        _application_result(lambda: CouponAdminApplication().list_product_options(
            q=q,
            product_type=product_type,
            limit=limit,
            offset=offset,
        ))
    )


@router.get("/api/admin/coupons/{coupon_id}", name="api.admin_coupon_detail")
def get_coupon(coupon_id: int, request: Request) -> JSONResponse:
    return _response(_application_result(lambda: CouponAdminApplication().get_coupon(coupon_id)))


@router.put("/api/admin/coupons/{coupon_id}", name="api.admin_coupon_update")
def update_coupon(coupon_id: int, request: Request, payload: CouponUpsertRequest) -> JSONResponse:
    if token_error := _unsafe_action_error(request):
        return token_error
    return _response(_application_result(lambda: _write_application(request).update_coupon(coupon_id, payload)))


@router.delete("/api/admin/coupons/{coupon_id}", name="api.admin_coupon_delete")
def delete_coupon(coupon_id: int, request: Request) -> JSONResponse:
    if token_error := _unsafe_action_error(request):
        return token_error
    return _response(_application_result(lambda: _write_application(request).delete_coupon(coupon_id)))


@router.post("/api/admin/coupons/{coupon_id}/publish", name="api.admin_coupon_publish")
def publish_coupon(coupon_id: int, request: Request) -> JSONResponse:
    if token_error := _unsafe_action_error(request):
        return token_error
    return _response(_application_result(lambda: _write_application(request).publish_coupon(coupon_id)))


@router.post("/api/admin/coupons/{coupon_id}/stop", name="api.admin_coupon_stop")
def stop_coupon(coupon_id: int, request: Request) -> JSONResponse:
    if token_error := _unsafe_action_error(request):
        return token_error
    return _response(_application_result(lambda: _write_application(request).stop_coupon(coupon_id)))


@router.post("/api/admin/coupons/{coupon_id}/archive", name="api.admin_coupon_archive")
def archive_coupon(coupon_id: int, request: Request) -> JSONResponse:
    if token_error := _unsafe_action_error(request):
        return token_error
    return _response(_application_result(lambda: _write_application(request).archive_coupon(coupon_id)))


@router.post("/api/admin/coupons/{coupon_id}/copy", name="api.admin_coupon_copy")
def copy_coupon(coupon_id: int, request: Request) -> JSONResponse:
    if token_error := _unsafe_action_error(request):
        return token_error
    return _response(
        _application_result(lambda: _write_application(request).copy_coupon(coupon_id)),
        status_code=201,
    )


@router.get("/api/admin/coupons/{coupon_id}/share", name="api.admin_coupon_share")
def get_coupon_share(coupon_id: int, request: Request) -> JSONResponse:
    return _response(
        _application_result(lambda: CouponAdminApplication().get_share(
            coupon_id,
            request_base_url=canonical_public_base_url(request),
        ))
    )


@router.get("/api/admin/coupons/{coupon_id}/claims", name="api.admin_coupon_claims")
def list_coupon_claims(
    coupon_id: int,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    return _response(
        _application_result(lambda: CouponAdminApplication().list_claims(coupon_id, limit=limit, offset=offset))
    )
