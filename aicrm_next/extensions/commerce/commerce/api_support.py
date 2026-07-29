from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.admin_shell_contract import shell_context
from aicrm_next.platform.shared.runtime_settings import (
    managed_runtime_setting,
    startup_environment_setting,
)
from aicrm_next.platform.shared.share_qr import jpeg_qr_data_url

from .admin_transactions import (
    default_filters,
    list_wechat_product_options,
)
from .admin_transaction_detail import (
    PaymentProviderStatusMapper,
    provider_config,
)
from .api_contract import checkout_order_headers as _checkout_order_headers
from .domain import has_product_page_material

def _admin_api_error(*, error_code: str, message: str, source_status: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error_code": error_code,
            "message": message,
            "route_owner": "ai_crm_next",
            "source_status": source_status,
            "fallback_used": False,
        },
        status_code=status_code,
        headers=_payment_final_headers(),
    )

def _checkout_order_side_effects(*, order_create_executed: str | bool = False) -> dict:
    return {
        "fallback_used": False,
        "real_external_call_executed": False,
        "payment_request_executed": False,
        "real_wechat_pay_executed": False,
        "real_alipay_executed": False,
        "order_create_executed": order_create_executed,
    }

def _checkout_order_options_payload(route: str, methods: list[str], *, adapter_mode: str = "fake/real_blocked") -> dict:
    return {
        "ok": True,
        "route": route,
        "methods": methods,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "adapter_mode": adapter_mode,
        "side_effect_safety": _checkout_order_side_effects(),
    }

def _checkout_order_error_payload(
    *,
    error_code: str,
    message: str,
    method: str,
    path: str,
    source_status: str,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error_code": error_code,
            "message": message,
            "method": method.upper(),
            "path": path,
            "source_status": source_status,
            "route_owner": "ai_crm_next",
            **_checkout_order_side_effects(),
        },
        status_code=status_code,
        headers=_checkout_order_headers(),
    )

def _provider_payment_headers(*, notify_executed: str = "false", return_executed: str = "false") -> dict[str, str]:
    return {
        "X-AICRM-Route-Owner": "ai_crm_next",
        "X-AICRM-Fallback-Used": "false",
        "X-AICRM-Real-External-Call-Executed": "false",
        "X-AICRM-Real-Payment-Notify-Executed": "false",
        "X-AICRM-Provider-Signature-Verified": "false",
        "X-AICRM-Payment-Notify-Executed": notify_executed,
        "X-AICRM-Payment-Return-Executed": return_executed,
    }

def _provider_payment_side_effects(*, notify_executed: str | bool = False, return_executed: str | bool = False) -> dict:
    return {
        "fallback_used": False,
        "real_external_call_executed": False,
        "real_payment_notify_executed": False,
        "real_wechat_pay_executed": False,
        "real_alipay_executed": False,
        "provider_signature_verified": False,
        "payment_notify_executed": notify_executed,
        "payment_return_executed": return_executed,
    }

def _provider_payment_options_payload(route: str, methods: list[str], *, source_status: str) -> dict:
    return {
        "ok": True,
        "route": route,
        "methods": methods,
        "source_status": source_status,
        "route_owner": "ai_crm_next",
        "adapter_mode": "fake/real_blocked",
        **_provider_payment_side_effects(),
    }

def _provider_payment_error_payload(
    *,
    error_code: str,
    message: str,
    method: str,
    path: str,
    source_status: str,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error_code": error_code,
            "message": message,
            "method": method.upper(),
            "path": path,
            "source_status": source_status,
            "route_owner": "ai_crm_next",
            **_provider_payment_side_effects(),
        },
        status_code=status_code,
        headers=_provider_payment_headers(),
    )

def _bool_header(value: bool) -> str:
    return "true" if value else "false"

def _payment_final_headers(*, real_external_call_executed: bool = False, real_refund_executed: bool = False) -> dict[str, str]:
    return {
        "X-AICRM-Route-Owner": "ai_crm_next",
        "X-AICRM-Fallback-Used": "false",
        "X-AICRM-Real-External-Call-Executed": _bool_header(real_external_call_executed),
        "X-AICRM-Payment-Request-Executed": "false",
        "X-AICRM-Real-WeChat-Pay-Executed": "false",
        "X-AICRM-Real-Alipay-Executed": "false",
        "X-AICRM-Provider-Signature-Verified": "false",
        "X-AICRM-Real-Refund-Executed": _bool_header(real_refund_executed),
    }

def _payment_final_side_effects(
    *,
    source_status: str = "next_payment_admin",
    real_external_call_executed: bool = False,
    real_refund_executed: bool = False,
) -> dict:
    return {
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": real_external_call_executed,
        "payment_request_executed": False,
        "real_wechat_pay_executed": False,
        "real_alipay_executed": False,
        "provider_signature_verified": False,
        "real_refund_executed": real_refund_executed,
        "source_status": source_status,
    }

def _payment_final_payload(
    payload: dict,
    *,
    source_status: str = "next_payment_admin",
    real_external_call_executed: bool = False,
    real_refund_executed: bool = False,
) -> dict:
    result = dict(payload)
    result.update(
        _payment_final_side_effects(
            source_status=source_status,
            real_external_call_executed=real_external_call_executed,
            real_refund_executed=real_refund_executed,
        )
    )
    return result

def _payment_final_blocked_payload(
    *,
    error_code: str,
    message: str,
    method: str,
    path: str,
    source_status: str,
    status_code: int = 410,
    replacement: str = "",
) -> JSONResponse:
    body = {
        "ok": False,
        "error_code": error_code,
        "message": message,
        "method": method.upper(),
        "path": path,
        "replacement": replacement,
        "adapter_mode": "real_blocked",
        **_payment_final_side_effects(source_status=source_status),
    }
    return JSONResponse(body, status_code=status_code, headers=_payment_final_headers())

def _payment_final_options_payload(route: str, methods: list[str], *, source_status: str) -> dict:
    return {
        "ok": True,
        "route": route,
        "methods": methods,
        "adapter_mode": "real_blocked",
        **_payment_final_side_effects(source_status=source_status),
    }

def _external_base_url(request: Request) -> str:
    configured = str(
        startup_environment_setting("AICRM_PUBLIC_BASE_URL")
        or startup_environment_setting("PUBLIC_BASE_URL")
        or startup_environment_setting("APP_BASE_URL")
        or ""
    ).strip()
    return configured.rstrip("/") if configured else str(request.base_url).rstrip("/")

def _refund_notify_url(request: Request) -> str:
    configured = str(
        managed_runtime_setting("WECHAT_PAY_REFUND_NOTIFY_URL") or ""
    ).strip()
    return configured or f"{_external_base_url(request)}/api/h5/wechat-pay/refund/notify"

def _product_admin_context(
    request: Request,
    *,
    page_title: str,
    page_summary: str,
    mode: str,
    product: dict | None = None,
) -> dict:
    context = shell_context(
        request=request,
        page_title=page_title,
        page_summary=page_summary,
        active_endpoint="api.admin_wechat_pay_products_page",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
        {"label": "商品管理", "href": request.url_for("api.admin_wechat_pay_products_page")},
    ]
    if mode != "list":
        context["breadcrumbs"].append({"label": "创建商品" if mode == "new" else "编辑商品"})
    context.update(
        {
            "product_page_mode": mode,
            "initial_product": jsonable_encoder(product or {}),
            "initial_product_json": json.dumps(jsonable_encoder(product or {}), ensure_ascii=False),
        }
    )
    return context

def _share_payload(request: Request, product: dict) -> dict:
    product_code = str(product.get("product_code") or "")
    public_path = "p" if has_product_page_material(product) else "pay"
    url = str(request.base_url).rstrip("/") + f"/{public_path}/{quote(product_code)}"
    return {
        "product_id": str(product.get("id") or ""),
        "product_code": product_code,
        "product_name": str(product.get("title") or ""),
        "url": url,
        "qr_data_url": jpeg_qr_data_url(url),
    }

def _transaction_admin_context(request: Request, *, provider: str, detail_order: dict | None = None, detail_error: str = "") -> dict:
    config = provider_config(provider)
    page_title = f"{config.provider_label}交易管理" if not detail_order else f"{config.provider_label}订单详情"
    page_summary = (
        f"按订单创建时间检索{config.provider_label}订单，查看商户单号、平台单号、商品、客户、金额、状态与回调摘要。"
        if not detail_order
        else f"核对{config.provider_label}订单状态、客户身份、回调摘要与事件时间线。"
    )
    if provider == "alipay":
        active_endpoint = "api.admin_alipay_transactions_page"
    elif provider == "wechat_shop":
        active_endpoint = "api.admin_orders_page"
    else:
        active_endpoint = "api.admin_wechat_pay_transactions_page"
    context = shell_context(
        request=request,
        page_title=page_title,
        page_summary=page_summary,
        active_endpoint=active_endpoint,
    )
    list_href = str(request.url_for(active_endpoint))
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
        {"label": f"{config.provider_label}交易管理", "href": list_href if detail_order or detail_error else ""},
    ]
    if detail_order or detail_error:
        context["breadcrumbs"].append({"label": "订单详情"})
    context.update(
        {
            "payment_provider": provider,
            "payment_provider_label": config.provider_label,
            "provider_platform_no_label": config.platform_no_label,
            "list_page_url": config.page_path,
            "list_api_url": "/api/admin/orders" if provider == "wechat_shop" else config.api_path,
            "fixed_provider": provider,
            "export_api_url": "/api/admin/wechat-pay/order-exports" if provider == "wechat" else "/api/admin/exports",
            "default_filters": default_filters(),
            "status_options": [{"value": key, "label": label} for key, label in PaymentProviderStatusMapper.LABELS.items()],
            "product_options": list_wechat_product_options() if provider == "wechat" else [],
            "detail_order": detail_order,
            "detail_error": detail_error,
        }
    )
    return context

def _unified_orders_context(request: Request) -> dict:
    context = shell_context(
        request=request,
        page_title="交易管理",
        page_summary="聚合微信支付、支付宝和微信小店订单，按支付渠道查看订单号、商品、金额、状态和创建时间。",
        active_endpoint="api.admin_orders_page",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
        {"label": "交易管理", "href": ""},
    ]
    context.update(
        {
            "list_api_url": "/api/admin/orders",
            "export_api_url": "/api/admin/exports",
            "default_filters": default_filters(),
            "status_options": [{"value": key, "label": label} for key, label in PaymentProviderStatusMapper.LABELS.items()],
            "provider_options": [
                {"value": "all", "label": "全部渠道"},
                {"value": "wechat", "label": "微信支付"},
                {"value": "alipay", "label": "支付宝"},
                {"value": "wechat_shop", "label": "微信小店"},
            ],
        }
    )
    return context
