from __future__ import annotations

import json
import logging
from pathlib import Path
from time import perf_counter
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aicrm_next.extensions.commerce.service_period_grid_auth import admin_auth_enforcement_enabled, current_auth_context
from aicrm_next.admin_shell_contract import shell_context
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.safe_logging import safe_log_exception, safe_log_fields
from aicrm_next.shared.share_qr import svg_qr_data_url
from aicrm_next.shared.sync_request import read_request_json
from aicrm_next.identity_contact.wechat_unionid_guard import evaluate_wechat_unionid_access

from .application import (
    CopyServicePeriodProductCommand,
    CreateServicePeriodMemberViewCommand,
    CreateServicePeriodProductCommand,
    DeleteServicePeriodMemberViewCommand,
    DeleteServicePeriodProductCommand,
    GetPublicServicePeriodProductQuery,
    GetServicePeriodProductBySlugQuery,
    GetServicePeriodMemberGridSchemaQuery,
    GetServicePeriodProductQuery,
    GetServicePeriodProductStatsQuery,
    GetServicePeriodPublicStateQuery,
    ListServicePeriodMemberViewsQuery,
    ListServicePeriodMembersQuery,
    ListServicePeriodProductsQuery,
    QueryServicePeriodMemberGridQuery,
    SetServicePeriodProductEnabledCommand,
    UpdateServicePeriodMemberAllianceCommand,
    UpdateServicePeriodMemberViewCommand,
    UpdateServicePeriodMemberRemarkCommand,
    UpdateServicePeriodProductCommand,
)
from .dto import ServicePeriodProductCreateRequest, ServicePeriodProductUpdateRequest
from .member_grid import MemberViewConflictError
from aicrm_next.extensions.commerce.service_period_grid_ports import CollaboratorAccountDisabledError
from .member_grid_access import (
    MemberGridAccessConflictError,
    MemberGridAccessDeniedError,
    MemberGridShareGoneError,
)
from .member_grid_sharing import (
    MemberGridShareUnauthorizedError,
    PublicServicePeriodMemberGridService,
    ServicePeriodMemberGridAccessService,
)
from .public import render_service_period_pay_page, render_service_period_public_page


router = APIRouter()
LOGGER = logging.getLogger(__name__)
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_FRONTEND_COMPAT_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=[_TEMPLATES_DIR, _FRONTEND_COMPAT_TEMPLATES_DIR])


def route_headers() -> dict[str, str]:
    return {
        "X-AICRM-Route-Owner": "ai_crm_next",
        "X-AICRM-Fallback-Used": "false",
        "X-AICRM-Real-External-Call-Executed": "false",
        "X-AICRM-Payment-Request-Executed": "false",
        "X-AICRM-Order-Create-Executed": "false",
    }


def product_not_found_payload(path: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": "service_period_product_not_found",
        "error_code": "service_period_product_not_found",
        "message": "Service period product path is not configured.",
        "path": str(path or "").strip().strip("/"),
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
        "payment_request_executed": False,
        "order_create_executed": False,
    }


def _inactive_public_state(product: dict) -> dict:
    return {
        "ok": True,
        "available": False,
        "product": {
            "title": product.get("title") or product.get("name"),
            "price_cents": int(product.get("price_cents") or product.get("amount_total") or 0),
            "currency": str(product.get("currency") or "CNY"),
            "duration_days": int(product.get("duration_days") or 0),
        },
        "service_product": product,
        "entitlement": {"status": "unavailable", "remaining_days": 0, "end_at": ""},
        "lead_qr": {},
        "cta_text": "暂未开放",
        "checkout_url": "",
        "create_order_url": "",
    }


def _service_period_checkout_product(product: dict, public_state: dict | None = None) -> dict:
    trade_product = dict(product.get("trade_product") or {})
    price = int(product.get("price_cents") or product.get("amount_total") or trade_product.get("price_cents") or trade_product.get("amount_total") or 0)
    title = str(product.get("title") or product.get("name") or trade_product.get("title") or trade_product.get("name") or "周期商品")
    return {
        **trade_product,
        "product_code": str(trade_product.get("product_code") or product.get("product_code") or ""),
        "title": title,
        "name": title,
        "price_cents": price,
        "amount_total": price,
        "currency": str(product.get("currency") or trade_product.get("currency") or "CNY"),
        "buy_button_text": str((public_state or {}).get("cta_text") or "确认支付"),
        "require_mobile": bool(trade_product.get("require_mobile") or product.get("require_mobile")),
    }


def _service_period_checkout_state(product: dict, request: Request, public_state: dict) -> dict:
    from aicrm_next.extensions.commerce.commerce.coupons.application import target_ref_for_product_id

    identity = h5_wechat_pay.h5_payment_identity_from_request(request)
    link_slug = str(product.get("link_slug") or "").strip()
    checkout_path = f"/s/{link_slug}/pay"
    public_path = f"/s/{quote(link_slug, safe='')}"
    access = evaluate_wechat_unionid_access(
        identity,
        is_wechat_browser=h5_wechat_pay._is_wechat_browser(request),
        oauth_start_url=h5_wechat_pay.payment_oauth_start_url(checkout_path),
    )
    checkout_product = _service_period_checkout_product(product, public_state)
    coupon_target_ref = target_ref_for_product_id(product.get("trade_product_id") or checkout_product.get("id"))
    return {
        "product": {
            "product_code": str(checkout_product.get("product_code") or ""),
            "name": str(checkout_product.get("title") or checkout_product.get("name") or ""),
            "amount_total": int(checkout_product.get("price_cents") or checkout_product.get("amount_total") or 0),
            "currency": str(checkout_product.get("currency") or "CNY"),
        },
        "identity_ready": access.allowed,
        "identity_error": access.error,
        "identity_message": access.message,
        "is_wechat_browser": h5_wechat_pay._is_wechat_browser(request),
        "oauth_start_url": h5_wechat_pay.payment_oauth_start_url(checkout_path),
        "create_order_url": f"/api/h5/service-period-products/{link_slug}/wechat-pay/jsapi/orders",
        "status_url_template": "/api/h5/wechat-pay/orders/{out_trade_no}",
        "post_paid_redirect_url": public_path,
        "enabled": h5_wechat_pay._env_bool("WECHAT_PAY_ENABLED", False),
        "require_mobile": bool(checkout_product.get("require_mobile")),
        "cta_text": str(public_state.get("cta_text") or "确认支付"),
        "completion_target": checkout_product.get("completion_target") or checkout_product.get("completion_target_json") or {},
        "completion_action": checkout_product.get("completion_action") or {"type": "default", "redirect_url": ""},
        "paid_order": None,
        "price_display": f"{str(checkout_product.get('currency') or 'CNY')} {int(checkout_product.get('price_cents') or 0) / 100:.2f}",
        "context_token": "",
        "context_status": "missing",
        "coupon_target_ref": coupon_target_ref,
        "available_coupon_url": f"/api/h5/coupons/available?target_ref={quote(coupon_target_ref, safe='')}",
    }


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (MemberViewConflictError, MemberGridAccessConflictError, CollaboratorAccountDisabledError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, MemberGridAccessDeniedError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ContractError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    safe_log_exception(LOGGER, "service period api unexpected error", exc)
    raise HTTPException(status_code=500, detail={"error_code": "service_period_internal_error", "message": "internal service period error"}) from exc


def _payload(data: dict) -> dict:
    return {
        **data,
        "route_owner": "ai_crm_next",
        "source_status": "next_service_period",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


def _actor(request: Request) -> str:
    context = getattr(request.state, "auth_context", None)
    return str(getattr(context, "principal_id", "") or "system")


def _grid_access_service(request: Request) -> ServicePeriodMemberGridAccessService:
    factory = getattr(request.app.state, "service_period_member_grid_access_service_factory", None)
    return factory() if callable(factory) else ServicePeriodMemberGridAccessService()


def _require_grid_access(request: Request, service_product_id: str, permission: str):
    return _grid_access_service(request).require(
        service_product_id,
        permission,
        context=current_auth_context(request),
        auth_enforced=admin_auth_enforcement_enabled(),
    )


def _expected_version(payload: dict[str, object], *, label: str) -> int:
    try:
        version = int(payload.get("version") or 0)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label}版本无效") from exc
    if version < 1:
        raise ContractError(f"{label}版本无效")
    return version


def _public_grid_response(payload: dict, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder(
            {
                **payload,
                "route_owner": "ai_crm_next",
                "source_status": "next_service_period_public_grid",
                "fallback_used": False,
                "real_external_call_executed": False,
            }
        ),
        status_code=status_code,
        headers={**route_headers(), "Cache-Control": "no-store, private, max-age=0", "Pragma": "no-cache"},
    )


def _raise_public_grid_http(exc: Exception) -> None:
    if isinstance(exc, MemberGridShareUnauthorizedError):
        raise HTTPException(status_code=401, detail=str(exc), headers={"Cache-Control": "no-store"}) from exc
    if isinstance(exc, MemberGridShareGoneError):
        raise HTTPException(status_code=410, detail=str(exc), headers={"Cache-Control": "no-store"}) from exc
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=404, detail=str(exc), headers={"Cache-Control": "no-store"}) from exc
    if isinstance(exc, ContractError):
        raise HTTPException(status_code=400, detail=str(exc), headers={"Cache-Control": "no-store"}) from exc
    safe_log_exception(LOGGER, "service period public member grid unexpected error", exc)
    raise HTTPException(status_code=500, detail="public_member_grid_unavailable", headers={"Cache-Control": "no-store"}) from exc


def _admin_context(request: Request, *, page_title: str, page_summary: str, page_mode: str, product: dict | None = None) -> dict:
    context = shell_context(
        request=request,
        page_title=page_title,
        page_summary=page_summary,
        active_endpoint="api.admin_service_period_products_page",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
        {"label": "周期商品管理", "href": request.url_for("api.admin_service_period_products_page")},
    ]
    if page_mode != "list":
        context["breadcrumbs"].append({"label": page_title})
    context.update(
        {
            "page_mode": page_mode,
            "service_product_id": str((product or {}).get("id") or ""),
            "initial_product": jsonable_encoder(product or {}),
            "initial_product_json": json.dumps(jsonable_encoder(product or {}), ensure_ascii=False),
            "page_error": "",
        }
    )
    return context


def _member_grid_page_title(product: dict) -> str:
    title = str(product.get("title") or product.get("name") or "周期商品").strip()
    return title if title.endswith("数据") else f"{title}数据"


def _limit_member_grid_action_tokens(context: dict, access) -> None:
    tokens = dict(context.get("admin_action_tokens") or {})
    for key in list(tokens):
        is_view_write = "/member-views" in key and not key.startswith("GET ")
        is_cell_write = "/members/{unionid}/" in key
        is_share_write = "/member-grid/collaborators" in key or "/member-grid/external-share" in key
        if (is_view_write or is_cell_write) and not access.can_edit:
            tokens.pop(key, None)
        elif is_share_write and not access.can_manage_share:
            tokens.pop(key, None)
    context["admin_action_tokens"] = tokens


@router.get("/admin/service-period-products", response_class=HTMLResponse, name="api.admin_service_period_products_page")
def admin_service_period_products_page(request: Request):
    try:
        payload = ListServicePeriodProductsQuery()(limit=100, offset=0)
    except Exception as exc:
        payload = {"ok": False, "items": [], "total": 0, "page_error": str(exc)}
    context = _admin_context(
        request,
        page_title="周期商品管理",
        page_summary="创建、编辑和上下架周期商品。",
        page_mode="list",
    )
    context.update(
        {
            "products": payload.get("items") or [],
            "product_total": int(payload.get("total") or 0),
            "page_error": str(payload.get("page_error") or ""),
        }
    )
    return templates.TemplateResponse(request, "service_period_products.html", context, status_code=200 if payload.get("ok", True) else 503)


@router.get("/admin/service-period-products/new", response_class=HTMLResponse, name="api.admin_service_period_product_new_page")
def admin_service_period_product_new_page(request: Request):
    return templates.TemplateResponse(
        request,
        "service_period_products.html",
        _admin_context(
            request,
            page_title="创建周期商品",
            page_summary="配置商品名称、价格、有效期和绑定会员设置。",
            page_mode="new",
        ),
    )


@router.get("/admin/service-period-products/{service_product_id}/edit", response_class=HTMLResponse, name="api.admin_service_period_product_edit_page")
def admin_service_period_product_edit_page(request: Request, service_product_id: str):
    try:
        product = GetServicePeriodProductQuery()(service_product_id)["product"]
        status_code = 200
    except Exception as exc:
        product = {}
        status_code = 404
        page_error = str(exc)
    else:
        page_error = ""
    context = _admin_context(
        request,
        page_title=f"编辑周期商品 {product.get('product_code')}" if product.get("product_code") else "编辑周期商品",
        page_summary="维护周期商品名称、价格、有效期和上架状态。",
        page_mode="edit",
        product=product,
    )
    context["page_error"] = page_error
    return templates.TemplateResponse(request, "service_period_products.html", context, status_code=status_code)


@router.get("/admin/service-period-products/{service_product_id}/data", response_class=HTMLResponse, name="api.admin_service_period_product_data_page")
def admin_service_period_product_data_page(request: Request, service_product_id: str):
    access = None
    try:
        access = _require_grid_access(request, service_product_id, "read")
        product = GetServicePeriodProductQuery()(service_product_id)["product"]
        status_code = 200
        page_error = ""
    except Exception as exc:
        product = {}
        status_code = 403 if isinstance(exc, MemberGridAccessDeniedError) else 404
        page_error = str(exc)
    context = _admin_context(
        request,
        page_title=_member_grid_page_title(product),
        page_summary="按视图筛选、排序和分组周期商品会员数据。",
        page_mode="data",
        product=product,
    )
    context.update(
        {
            "page_error": page_error,
            "compact_shell": bool(access and access.compact_shell),
            "member_grid_access": access.to_dict() if access else {},
        }
    )
    if access:
        _limit_member_grid_action_tokens(context, access)
    return templates.TemplateResponse(request, "service_period_member_grid.html", context, status_code=status_code)


@router.get("/api/admin/service-period-products")
def list_service_period_products(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
    try:
        return _payload(ListServicePeriodProductsQuery()(limit=limit, offset=offset))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/service-period-products")
def create_service_period_product(payload: ServicePeriodProductCreateRequest) -> JSONResponse:
    try:
        result = _payload(CreateServicePeriodProductCommand()(payload))
    except Exception as exc:
        _raise_http(exc)
    return JSONResponse(jsonable_encoder(result), status_code=201)


@router.get("/api/admin/service-period-products/membership-configs")
def list_service_period_membership_configs() -> dict:
    return _payload({"ok": True, "items": []})


@router.get("/api/admin/service-period-products/{service_product_id}")
def get_service_period_product(service_product_id: str) -> dict:
    try:
        return _payload(GetServicePeriodProductQuery()(service_product_id))
    except Exception as exc:
        _raise_http(exc)


@router.put("/api/admin/service-period-products/{service_product_id}")
def update_service_period_product(service_product_id: str, payload: ServicePeriodProductUpdateRequest) -> dict:
    try:
        return _payload(UpdateServicePeriodProductCommand()(service_product_id, payload))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/service-period-products/{service_product_id}/copy")
def copy_service_period_product(service_product_id: str) -> JSONResponse:
    try:
        result = _payload(CopyServicePeriodProductCommand()(service_product_id))
    except Exception as exc:
        _raise_http(exc)
    return JSONResponse(jsonable_encoder(result), status_code=201)


@router.post("/api/admin/service-period-products/{service_product_id}/enable")
def enable_service_period_product(service_product_id: str) -> dict:
    try:
        return _payload(SetServicePeriodProductEnabledCommand()(service_product_id, enabled=True))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/service-period-products/{service_product_id}/disable")
def disable_service_period_product(service_product_id: str) -> dict:
    try:
        return _payload(SetServicePeriodProductEnabledCommand()(service_product_id, enabled=False))
    except Exception as exc:
        _raise_http(exc)


@router.delete("/api/admin/service-period-products/{service_product_id}")
def delete_service_period_product(service_product_id: str) -> dict:
    try:
        return _payload(DeleteServicePeriodProductCommand()(service_product_id))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/service-period-products/{service_product_id}/share")
def share_service_period_product(service_product_id: str, request: Request) -> dict:
    try:
        product = GetServicePeriodProductQuery()(service_product_id)["product"]
    except Exception as exc:
        _raise_http(exc)
    url = str(request.base_url).rstrip("/") + f"/s/{quote(str(product.get('link_slug') or ''))}"
    return _payload(
        {
            "ok": True,
            "share": {
                "service_product_id": str(product.get("id") or ""),
                "trade_product_id": str(product.get("trade_product_id") or ""),
                "product_code": str(product.get("product_code") or ""),
                "product_name": str(product.get("title") or product.get("name") or ""),
                "url": url,
                "qr_data_url": svg_qr_data_url(url),
            },
        }
    )


@router.get("/api/admin/service-period-products/{service_product_id}/stats")
def service_period_product_stats(service_product_id: str) -> dict:
    try:
        return _payload(GetServicePeriodProductStatsQuery()(service_product_id))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/service-period-products/{service_product_id}/members")
def service_period_product_members(
    service_product_id: str,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    try:
        return _payload(ListServicePeriodMembersQuery()(service_product_id, status=status, limit=limit, offset=offset))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/service-period-products/{service_product_id}/member-grid/access")
def service_period_member_grid_access(service_product_id: str, request: Request) -> dict:
    try:
        return _payload(
            _grid_access_service(request).access_payload(
                service_product_id,
                context=current_auth_context(request),
                auth_enforced=admin_auth_enforcement_enabled(),
            )
        )
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/service-period-products/{service_product_id}/member-grid/schema")
def service_period_member_grid_schema(service_product_id: str, request: Request) -> dict:
    try:
        _require_grid_access(request, service_product_id, "read")
        return _payload(GetServicePeriodMemberGridSchemaQuery()(service_product_id))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/service-period-products/{service_product_id}/member-views")
def list_service_period_member_views(service_product_id: str, request: Request) -> dict:
    try:
        _require_grid_access(request, service_product_id, "read")
        return _payload(ListServicePeriodMemberViewsQuery()(service_product_id))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/service-period-products/{service_product_id}/member-views")
def create_service_period_member_view(service_product_id: str, request: Request) -> JSONResponse:
    try:
        _require_grid_access(request, service_product_id, "edit")
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        result = _payload(
            CreateServicePeriodMemberViewCommand()(
                service_product_id,
                name=str(payload.get("name") or ""),
                config=payload.get("config") if isinstance(payload.get("config"), dict) else None,
                actor=_actor(request),
            )
        )
        LOGGER.info(
            "service_period_member_view_created",
            extra=safe_log_fields(
                service_product_id=service_product_id,
                view_id=(result.get("view") or {}).get("id"),
            ),
        )
    except Exception as exc:
        _raise_http(exc)
    return JSONResponse(jsonable_encoder(result), status_code=201)


@router.put("/api/admin/service-period-products/{service_product_id}/member-views/{view_id}")
def update_service_period_member_view(service_product_id: str, view_id: str, request: Request) -> dict:
    try:
        _require_grid_access(request, service_product_id, "edit")
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        config = payload.get("config")
        if not isinstance(config, dict):
            raise ContractError("视图配置不能为空")
        try:
            expected_version = int(payload.get("version") or 0)
        except (TypeError, ValueError) as exc:
            raise ContractError("视图版本无效") from exc
        if expected_version < 1:
            raise ContractError("视图版本无效")
        result = _payload(
            UpdateServicePeriodMemberViewCommand()(
                service_product_id,
                view_id,
                name=str(payload.get("name") or ""),
                config=config,
                expected_version=expected_version,
                actor=_actor(request),
            )
        )
        LOGGER.info(
            "service_period_member_view_updated",
            extra=safe_log_fields(service_product_id=service_product_id, view_id=view_id),
        )
        return result
    except Exception as exc:
        _raise_http(exc)


@router.delete("/api/admin/service-period-products/{service_product_id}/member-views/{view_id}")
def delete_service_period_member_view(service_product_id: str, view_id: str, request: Request) -> dict:
    try:
        _require_grid_access(request, service_product_id, "edit")
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        try:
            expected_version = int(payload.get("version") or 0)
        except (TypeError, ValueError) as exc:
            raise ContractError("视图版本无效") from exc
        if expected_version < 1:
            raise ContractError("视图版本无效")
        result = _payload(
            DeleteServicePeriodMemberViewCommand()(
                service_product_id,
                view_id,
                expected_version=expected_version,
            )
        )
        LOGGER.info(
            "service_period_member_view_deleted",
            extra=safe_log_fields(service_product_id=service_product_id, view_id=view_id),
        )
        return result
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/service-period-products/{service_product_id}/member-grid/query")
def query_service_period_member_grid(service_product_id: str, request: Request) -> dict:
    started_at = perf_counter()
    try:
        _require_grid_access(request, service_product_id, "read")
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        config = payload.get("config")
        if config is not None and not isinstance(config, dict):
            raise ContractError("视图配置格式错误")
        result = _payload(
            QueryServicePeriodMemberGridQuery()(
                service_product_id,
                config=config,
                limit=int(payload.get("limit") or 100),
                cursor=str(payload.get("cursor") or ""),
            )
        )
        LOGGER.info(
            "service_period_member_grid_queried",
            extra=safe_log_fields(
                service_product_id=service_product_id,
                duration_ms=round((perf_counter() - started_at) * 1000, 2),
                row_count=len(result.get("rows") or []),
            ),
        )
        return result
    except (TypeError, ValueError):
        _raise_http(ContractError("分页参数无效"))
    except Exception as exc:
        _raise_http(exc)


@router.put("/api/admin/service-period-products/{service_product_id}/members/{unionid}/remark")
def update_service_period_member_remark(service_product_id: str, unionid: str, request: Request) -> dict:
    try:
        _require_grid_access(request, service_product_id, "edit")
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        return _payload(UpdateServicePeriodMemberRemarkCommand()(service_product_id, unionid, remark=str(payload.get("remark") or "")))
    except Exception as exc:
        _raise_http(exc)


@router.put("/api/admin/service-period-products/{service_product_id}/members/{unionid}/alliance")
def update_service_period_member_alliance(service_product_id: str, unionid: str, request: Request) -> dict:
    try:
        _require_grid_access(request, service_product_id, "edit")
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        return _payload(
            UpdateServicePeriodMemberAllianceCommand()(
                service_product_id,
                unionid,
                alliance=str(payload.get("alliance") or ""),
            )
        )
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/service-period-products/{service_product_id}/member-grid/share-settings")
def get_service_period_member_grid_share_settings(service_product_id: str, request: Request) -> dict:
    try:
        result = _grid_access_service(request).share_settings(
            service_product_id,
            context=current_auth_context(request),
            auth_enforced=admin_auth_enforcement_enabled(),
            base_url=str(request.base_url),
        )
        return _payload(result)
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/service-period-products/{service_product_id}/member-grid/collaborators")
def create_service_period_member_grid_collaborator(service_product_id: str, request: Request) -> JSONResponse:
    try:
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        result = _grid_access_service(request).create_collaborator(
            service_product_id,
            wecom_userid=str(payload.get("wecom_userid") or ""),
            permission=str(payload.get("permission") or "read"),
            actor=_actor(request),
            context=current_auth_context(request),
            auth_enforced=admin_auth_enforcement_enabled(),
        )
        LOGGER.info(
            "service_period_member_grid_collaborator_created",
            extra=safe_log_fields(
                service_product_id=service_product_id,
                collaborator_id=(result.get("collaborator") or {}).get("id"),
            ),
        )
        return JSONResponse(jsonable_encoder(_payload(result)), status_code=201)
    except Exception as exc:
        _raise_http(exc)


@router.put("/api/admin/service-period-products/{service_product_id}/member-grid/collaborators/{collaborator_id}")
def update_service_period_member_grid_collaborator(
    service_product_id: str,
    collaborator_id: str,
    request: Request,
) -> dict:
    try:
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        result = _grid_access_service(request).update_collaborator(
            service_product_id,
            collaborator_id,
            permission=str(payload.get("permission") or ""),
            expected_version=_expected_version(payload, label="协作者"),
            actor=_actor(request),
            context=current_auth_context(request),
            auth_enforced=admin_auth_enforcement_enabled(),
        )
        return _payload(result)
    except Exception as exc:
        _raise_http(exc)


@router.delete("/api/admin/service-period-products/{service_product_id}/member-grid/collaborators/{collaborator_id}")
def delete_service_period_member_grid_collaborator(
    service_product_id: str,
    collaborator_id: str,
    request: Request,
) -> dict:
    try:
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        result = _grid_access_service(request).delete_collaborator(
            service_product_id,
            collaborator_id,
            expected_version=_expected_version(payload, label="协作者"),
            actor=_actor(request),
            context=current_auth_context(request),
            auth_enforced=admin_auth_enforcement_enabled(),
        )
        LOGGER.info(
            "service_period_member_grid_collaborator_deleted",
            extra=safe_log_fields(service_product_id=service_product_id, collaborator_id=collaborator_id),
        )
        return _payload(result)
    except Exception as exc:
        _raise_http(exc)


@router.put("/api/admin/service-period-products/{service_product_id}/member-grid/external-share")
def update_service_period_member_grid_external_share(service_product_id: str, request: Request) -> dict:
    try:
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        result = _grid_access_service(request).set_external_share(
            service_product_id,
            enabled=bool(payload.get("enabled")),
            expected_version=_expected_version(payload, label="外部分享"),
            actor=_actor(request),
            context=current_auth_context(request),
            auth_enforced=admin_auth_enforcement_enabled(),
            base_url=str(request.base_url),
        )
        LOGGER.info(
            "service_period_member_grid_external_share_updated",
            extra=safe_log_fields(
                service_product_id=service_product_id,
                enabled=bool((result.get("external_share") or {}).get("enabled")),
            ),
        )
        return _payload(result)
    except Exception as exc:
        _raise_http(exc)


@router.get("/shared/service-period-member-grid", response_class=HTMLResponse, name="api.public_service_period_member_grid_page")
def public_service_period_member_grid_page(request: Request):
    response = templates.TemplateResponse(
        request,
        "service_period_member_grid_public.html",
        {
            "request": request,
            "page_title": "周期商品数据",
            "page_summary": "外部只读分享",
        },
    )
    response.headers.update({**route_headers(), "Cache-Control": "no-store, private, max-age=0", "Pragma": "no-cache"})
    return response


@router.get("/api/public/service-period-member-grid/bootstrap")
def public_service_period_member_grid_bootstrap(request: Request) -> JSONResponse:
    try:
        payload = PublicServicePeriodMemberGridService().bootstrap(
            str(request.headers.get("X-AICRM-Grid-Share-Token") or "")
        )
        return _public_grid_response(payload)
    except Exception as exc:
        _raise_public_grid_http(exc)


@router.post("/api/public/service-period-member-grid/query")
def public_service_period_member_grid_query(request: Request) -> JSONResponse:
    try:
        body = read_request_json(request)
        payload = body if isinstance(body, dict) else {}
        unexpected = set(payload) - {"view_id", "cursor", "limit"}
        if unexpected:
            raise ContractError("公开只读查询不支持自定义筛选配置")
        try:
            limit = int(payload.get("limit") or 100)
        except (TypeError, ValueError) as exc:
            raise ContractError("分页参数无效") from exc
        result = PublicServicePeriodMemberGridService().query(
            str(request.headers.get("X-AICRM-Grid-Share-Token") or ""),
            view_id=str(payload.get("view_id") or ""),
            cursor=str(payload.get("cursor") or ""),
            limit=limit,
        )
        return _public_grid_response(result)
    except Exception as exc:
        _raise_public_grid_http(exc)


@router.get("/s/{link_slug}", response_class=HTMLResponse, name="api.public_service_period_product_page")
def public_service_period_product_page(request: Request, link_slug: str):
    try:
        product = GetPublicServicePeriodProductQuery()(link_slug)["product"]
    except NotFoundError:
        try:
            product = GetServicePeriodProductBySlugQuery()(link_slug)["product"]
        except NotFoundError:
            from aicrm_next.extensions.forms.questionnaire.api import public_questionnaire_h5_page

            try:
                return public_questionnaire_h5_page(request, link_slug)
            except HTTPException:
                raise
            except Exception:
                return HTMLResponse(
                    "<!doctype html><meta charset='utf-8'><main data-route-owner='ai_crm_next'>周期商品不存在</main>",
                    status_code=404,
                    headers=route_headers(),
                )
        state = _inactive_public_state(product)
        html = render_service_period_public_page(product, state)
        return templates.TemplateResponse(request, "service_period_public.html", {"request": request, "html": html}, headers=route_headers())
    except Exception as exc:
        _raise_http(exc)
    try:
        identity = h5_wechat_pay.h5_payment_identity_from_request(request)
        access = evaluate_wechat_unionid_access(
            identity,
            is_wechat_browser=h5_wechat_pay._is_wechat_browser(request),
            oauth_start_url=h5_wechat_pay.payment_oauth_start_url(
                f"/s/{quote(str(link_slug or '').strip(), safe='')}"
            ),
        )
        if h5_wechat_pay._is_wechat_browser(request) and not access.allowed:
            return RedirectResponse(
                access.oauth_start_url,
                status_code=302,
                headers=route_headers(),
            )
        state = GetServicePeriodPublicStateQuery()(link_slug, unionid=str(identity.get("unionid") or ""))
    except NotFoundError:
        state = _inactive_public_state(product)
    except Exception as exc:
        _raise_http(exc)
    html = render_service_period_public_page(product, state)
    return templates.TemplateResponse(request, "service_period_public.html", {"request": request, "html": html}, headers=route_headers())


@router.get("/s/{link_slug}/pay", response_class=HTMLResponse, name="api.public_service_period_pay_page")
def public_service_period_pay_page(request: Request, link_slug: str):
    try:
        product = GetPublicServicePeriodProductQuery()(link_slug)["product"]
        identity = h5_wechat_pay.h5_payment_identity_from_request(request)
        access = evaluate_wechat_unionid_access(
            identity,
            is_wechat_browser=h5_wechat_pay._is_wechat_browser(request),
            oauth_start_url=h5_wechat_pay.payment_oauth_start_url(
                f"/s/{quote(str(link_slug or '').strip(), safe='')}/pay"
            ),
        )
        if h5_wechat_pay._is_wechat_browser(request) and not access.allowed:
            return RedirectResponse(access.oauth_start_url, status_code=302, headers=route_headers())
        public_state = GetServicePeriodPublicStateQuery()(link_slug, unionid=str(identity.get("unionid") or ""))
    except Exception as exc:
        if isinstance(exc, NotFoundError):
            return HTMLResponse(
                "<!doctype html><meta charset='utf-8'><main data-route-owner='ai_crm_next'>周期商品不存在</main>",
                status_code=404,
                headers=route_headers(),
            )
        _raise_http(exc)
    checkout_product = _service_period_checkout_product(product, public_state)
    checkout_state = _service_period_checkout_state(product, request, public_state)
    return HTMLResponse(render_service_period_pay_page(checkout_product, checkout_state), headers=route_headers())


@router.get("/api/h5/service-period-products/{link_slug}")
def public_service_period_product_state(request: Request, link_slug: str) -> dict:
    try:
        identity = h5_wechat_pay.h5_payment_identity_from_request(request)
        access = evaluate_wechat_unionid_access(
            identity,
            is_wechat_browser=h5_wechat_pay._is_wechat_browser(request),
            oauth_start_url=h5_wechat_pay.payment_oauth_start_url(
                f"/s/{quote(str(link_slug or '').strip(), safe='')}"
            ),
        )
        if not access.allowed:
            return JSONResponse(access.payload(), status_code=access.status_code, headers=route_headers())
        return _payload(GetServicePeriodPublicStateQuery()(link_slug, unionid=str(identity.get("unionid") or "")))
    except Exception as exc:
        if isinstance(exc, NotFoundError):
            return JSONResponse(product_not_found_payload(link_slug), status_code=404, headers=route_headers())
        _raise_http(exc)


@router.post("/api/h5/service-period-products/{link_slug}/wechat-pay/jsapi/orders")
def create_service_period_jsapi_order(request: Request, link_slug: str):
    try:
        body = read_request_json(request)
        product = GetPublicServicePeriodProductQuery()(link_slug)["product"]
        trade_product = dict(product.get("trade_product") or {})
        payload = body if isinstance(body, dict) else {}
        payload["product_code"] = trade_product.get("product_code") or product.get("product_code")
        return h5_wechat_pay.create_jsapi_order_response(
            request,
            payload,
            product_override=trade_product,
            checkout_return_path=f"/s/{product.get('link_slug')}/pay",
            allow_paid_reuse=False,
            order_source="service_period_checkout",
            request_meta_extra={
                "service_period_product": {
                    "service_product_id": str(product.get("id") or ""),
                    "trade_product_id": str(product.get("trade_product_id") or ""),
                    "duration_days": int(product.get("duration_days") or 0),
                }
            },
        )
    except Exception as exc:
        if isinstance(exc, NotFoundError):
            return JSONResponse(product_not_found_payload(link_slug), status_code=404, headers=route_headers())
        _raise_http(exc)
