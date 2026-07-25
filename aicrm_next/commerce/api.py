# ruff: noqa: F401
from __future__ import annotations

import csv
import io
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, Path as PathParam, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_shell import shell_context
from aicrm_next.shared.errors import NotFoundError
from aicrm_next.shared.pii_audit import infer_pii_result_count, set_pii_audit_result_count
from aicrm_next.shared.safe_logging import safe_log_exception
from aicrm_next.shared.share_qr import svg_qr_data_url
from aicrm_next.shared.sync_request import read_request_body, read_request_json

from .admin_transactions import (
    default_filters,
    create_wechat_refund_request,
    export_orders_csv,
    handle_wechat_refund_notify,
    list_wechat_admin_orders,
    list_wechat_product_options,
)
from .admin_transaction_detail import (
    CommerceAdminTransactionDetailReadModel,
    CommerceAdminTransactionListReadModel,
    PaymentProviderStatusMapper,
    provider_config,
)
from .admin_exports import create_export_job, get_export_job
from .api_contract import checkout_order_headers as _checkout_order_headers
from .api_contract import raise_http as _raise_http
from .admin_refunds import list_refunds as list_unified_refunds
from .admin_refunds import request_refund as request_unified_refund
from .admin_unified_orders import (
    customer_commerce_summary,
    get_order as get_unified_order,
    list_customer_orders,
    list_order_items as list_unified_order_items,
    list_orders as list_unified_orders,
    list_payments as list_unified_payments,
)
from .admin_webhooks import list_webhook_events, replay_webhook
from .external_push_admin import (
    ExternalPushAdminError,
    list_order_external_push_state,
    retry_order_delivery,
    send_product_external_push_test,
)
from .application import (
    CheckoutCommand,
    DeleteProductCommand,
    GetOrderQuery,
    GetProductQuery,
    GetPublicProductQuery,
    ListProductsQuery,
    NotifyPaymentCommand,
    PaymentReturnCommand,
    SetProductEnabledCommand,
    UpsertProductCommand,
)
from .domain import has_product_page_material
from .dto import CheckoutRequest, PaymentNotifyRequest, ProductUpsertRequest
from .external_orders import router as external_orders_router
from .repo import build_commerce_repository
from .wechat_shop_service import (
    handle_wechat_shop_notify,
    list_wechat_shop_events,
    list_wechat_shop_sync_runs,
    sanitize_wechat_shop_error,
    sync_wechat_shop_order,
    verify_echo as verify_wechat_shop_echo,
)

router = APIRouter()
router.include_router(external_orders_router)
_COMMERCE_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_FRONTEND_COMPAT_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=[_COMMERCE_TEMPLATES_DIR, _FRONTEND_COMPAT_TEMPLATES_DIR])
_EXTERNAL_PUSH_CALL_EXECUTED = bool(1)
logger = logging.getLogger(__name__)


from .api_support import (
    _admin_api_error,
    _checkout_order_side_effects,
    _checkout_order_options_payload,
    _checkout_order_error_payload,
    _provider_payment_headers,
    _provider_payment_side_effects,
    _provider_payment_options_payload,
    _provider_payment_error_payload,
    _bool_header,
    _payment_final_headers,
    _payment_final_side_effects,
    _payment_final_payload,
    _payment_final_blocked_payload,
    _payment_final_options_payload,
    _external_base_url,
    _refund_notify_url,
    _product_admin_context,
    _share_payload,
    _transaction_admin_context,
    _unified_orders_context,
)








































@router.get("/admin/wechat-pay/products", response_class=HTMLResponse, name="api.admin_wechat_pay_products_page")
def admin_wechat_pay_products_page(request: Request):
    try:
        payload = ListProductsQuery()(limit=100, offset=0)
    except Exception as exc:
        payload = {"ok": False, "items": [], "total": 0, "page_error": str(exc)}
    context = _product_admin_context(
        request,
        page_title="微信支付商品管理",
        page_summary="创建、编辑和上下架微信支付商品。",
        mode="list",
    )
    products = payload.get("items") or []
    context.update(
        {
            "initial_products": jsonable_encoder(products),
            "initial_products_json": json.dumps(jsonable_encoder(products), ensure_ascii=False),
            "product_total": int(payload.get("total") or len(products)),
            "page_error": str(payload.get("page_error") or ""),
        }
    )
    return templates.TemplateResponse(request, "wechat_products.html", context, status_code=200 if payload.get("ok", True) else 503)


@router.get("/admin/wechat-pay/products/new", response_class=HTMLResponse, name="api.admin_wechat_pay_product_new_page")
def admin_wechat_pay_product_new_page(request: Request):
    context = _product_admin_context(
        request,
        page_title="创建微信支付商品",
        page_summary="配置商品编码、名称、价格与上架状态。",
        mode="new",
    )
    return templates.TemplateResponse(request, "wechat_products.html", context)


@router.get("/admin/wechat-pay/products/{product_id}/edit", response_class=HTMLResponse, name="api.admin_wechat_pay_product_edit_page")
def admin_wechat_pay_product_edit_page(request: Request, product_id: str):
    try:
        product = GetProductQuery()(product_id)["product"]
    except Exception as exc:
        context = _product_admin_context(
            request,
            page_title="商品不存在",
            page_summary="当前没有找到这个商品。",
            mode="edit",
        )
        context["page_error"] = str(exc)
        return templates.TemplateResponse(request, "wechat_products.html", context, status_code=404)
    context = _product_admin_context(
        request,
        page_title=f"编辑商品 {product.get('product_code')}",
        page_summary="维护商品名称、价格与上架状态。",
        mode="edit",
        product=product,
    )
    return templates.TemplateResponse(request, "wechat_products.html", context)


@router.get("/admin/wechat-pay/transactions", response_class=HTMLResponse, name="api.admin_wechat_pay_transactions_page")
def admin_wechat_transactions_page(request: Request):
    return templates.TemplateResponse(
        request,
        "wechat_transactions.html",
        _transaction_admin_context(request, provider="wechat"),
    )


@router.get("/admin/orders", response_class=HTMLResponse, name="api.admin_orders_page")
def admin_unified_orders_page(request: Request):
    return templates.TemplateResponse(request, "admin_orders.html", _unified_orders_context(request))


@router.get("/admin/wechat-pay/transactions/{order_id}", response_class=HTMLResponse, name="api.admin_wechat_pay_transaction_detail_page")
def admin_wechat_transaction_detail_page(request: Request, order_id: str) -> Response:
    try:
        order = CommerceAdminTransactionDetailReadModel("wechat").execute(order_id)["transaction"]
        status_code = 200
        context = _transaction_admin_context(request, provider="wechat", detail_order=order)
    except NotFoundError:
        status_code = 404
        context = _transaction_admin_context(request, provider="wechat", detail_error="订单不存在")
    return templates.TemplateResponse(
        request,
        "wechat_transactions.html",
        context,
        status_code=status_code,
    )


@router.get("/admin/wechat-shop/transactions", response_class=HTMLResponse, name="api.admin_wechat_shop_transactions_page")
def admin_wechat_shop_transactions_page(request: Request):
    return templates.TemplateResponse(
        request,
        "wechat_transactions.html",
        _transaction_admin_context(request, provider="wechat_shop"),
    )


@router.get("/admin/wechat-shop/transactions/{order_id}", response_class=HTMLResponse, name="api.admin_wechat_shop_transaction_detail_page")
def admin_wechat_shop_transaction_detail_page(request: Request, order_id: str) -> Response:
    try:
        order = CommerceAdminTransactionDetailReadModel("wechat_shop").execute(order_id)["transaction"]
        status_code = 200
        context = _transaction_admin_context(request, provider="wechat_shop", detail_order=order)
    except NotFoundError:
        status_code = 404
        context = _transaction_admin_context(request, provider="wechat_shop", detail_error="订单不存在")
    return templates.TemplateResponse(
        request,
        "wechat_transactions.html",
        context,
        status_code=status_code,
    )


@router.get("/admin/alipay/transactions", response_class=HTMLResponse, name="api.admin_alipay_transactions_page")
def admin_alipay_transactions_page(request: Request):
    return templates.TemplateResponse(
        request,
        "wechat_transactions.html",
        _transaction_admin_context(request, provider="alipay"),
    )


@router.get("/api/admin/wechat-pay/orders")
def list_wechat_admin_order_page(
    mobile: str | None = None,
    identity: str | None = None,
    transaction_id: str | None = None,
    product_code: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    return _payment_final_payload(
        list_wechat_admin_orders(
            {
                "mobile": mobile,
                "identity": identity,
                "transaction_id": transaction_id,
                "product_code": product_code,
                "created_from": created_from,
                "created_to": created_to,
                "status": status,
            },
            limit=limit,
            offset=offset,
        )
    )


@router.post("/api/admin/wechat-pay/order-exports")
def export_wechat_admin_orders(request: Request) -> Response:
    try:
        payload = read_request_json(request)
    except Exception:
        payload = {}
    csv_text = export_orders_csv(payload.get("filters") if isinstance(payload, dict) else {})
    set_pii_audit_result_count(request, max(0, sum(1 for _row in csv.reader(io.StringIO(csv_text))) - 1))
    return Response(
        csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            **_payment_final_headers(),
            "Content-Disposition": 'attachment; filename="wechat-pay-orders.csv"',
        },
    )


@router.get("/api/admin/wechat-pay/order-exports/{job_id}")
@router.get("/api/admin/wechat-pay/order-exports/{job_id}/download")
def deprecated_wechat_admin_order_export_job(job_id: str, request: Request) -> JSONResponse:
    return _payment_final_blocked_payload(
        error_code="admin_wechat_pay_export_job_removed",
        message="legacy export job download path is removed; use POST /api/admin/wechat-pay/order-exports for immediate CSV export",
        method=request.method,
        path=f"/api/admin/wechat-pay/order-exports/{job_id}" + ("/download" if request.url.path.endswith("/download") else ""),
        source_status="next_payment_admin_deprecated",
        replacement="/api/admin/wechat-pay/order-exports",
    )


@router.get("/api/admin/wechat-pay/orders/{order_id}/external-push-deliveries")
def list_wechat_order_external_push_deliveries(order_id: str) -> JSONResponse:
    try:
        payload = list_order_external_push_state(int(order_id))
    except ExternalPushAdminError as exc:
        return JSONResponse({"ok": False, "error": str(exc), **_payment_final_side_effects()}, status_code=404, headers=_payment_final_headers())
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), **_payment_final_side_effects()}, status_code=400, headers=_payment_final_headers())
    return JSONResponse(jsonable_encoder(_payment_final_payload(payload)), headers=_payment_final_headers())


@router.post("/api/admin/wechat-pay/orders/{order_id}/external-push-deliveries/{delivery_id}/retry")
def retry_wechat_order_external_push_delivery(order_id: str, delivery_id: str) -> JSONResponse:
    try:
        result = retry_order_delivery(int(order_id), delivery_id)
    except ExternalPushAdminError as exc:
        status_code = 404 if "不存在" in str(exc) else 400
        return JSONResponse({"ok": False, "error": str(exc), **_payment_final_side_effects()}, status_code=status_code, headers=_payment_final_headers())
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), **_payment_final_side_effects()}, status_code=400, headers=_payment_final_headers())
    return JSONResponse(
        jsonable_encoder(_payment_final_payload({"ok": True, "result": result}, real_external_call_executed=_EXTERNAL_PUSH_CALL_EXECUTED)),
        headers=_payment_final_headers(real_external_call_executed=_EXTERNAL_PUSH_CALL_EXECUTED),
    )


@router.post("/api/admin/wechat-pay/orders/{order_id}/refunds")
def request_wechat_admin_refund(order_id: str, request: Request) -> JSONResponse:
    try:
        payload = read_request_json(request)
    except Exception:
        payload = {}
    normalized_payload = dict(payload) if isinstance(payload, dict) else {}
    normalized_payload["refund_notify_url"] = _refund_notify_url(request)
    try:
        result = create_wechat_refund_request(order_id, normalized_payload)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), **_payment_final_side_effects()}, status_code=400, headers=_payment_final_headers())
    provider_refund_executed = bool((result.get("refund") or {}).get("provider_refund_executed"))
    return JSONResponse(
        _payment_final_payload(
            result,
            real_external_call_executed=provider_refund_executed,
            real_refund_executed=provider_refund_executed,
        ),
        headers=_payment_final_headers(
            real_external_call_executed=provider_refund_executed,
            real_refund_executed=provider_refund_executed,
        ),
    )


@router.post("/api/h5/wechat-pay/refund/notify")
def wechat_refund_notify(request: Request) -> JSONResponse:
    body = read_request_body(request)
    body_text = body.decode("utf-8")
    try:
        handle_wechat_refund_notify(body_text, dict(request.headers))
        return JSONResponse({"code": "SUCCESS", "message": "成功"})
    except Exception as exc:
        return JSONResponse({"code": "FAIL", "message": str(exc)}, status_code=401)


@router.get("/api/admin/wechat-pay/products")
def list_products(limit: int = 50, offset: int = 0) -> dict:
    try:
        return _payment_final_payload(ListProductsQuery()(limit=limit, offset=offset))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/wechat-pay/products/lead-channels")
def list_product_lead_channels() -> dict:
    try:
        return _payment_final_payload({"ok": True, "items": build_commerce_repository().list_lead_channels()})
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/wechat-pay/products/lead-plans")
def deprecated_product_lead_plans(request: Request) -> JSONResponse:
    return _payment_final_blocked_payload(
        error_code="admin_wechat_pay_lead_plans_removed",
        message="legacy lead-plans path is removed; use lead-channels for commerce product binding",
        method=request.method,
        path="/api/admin/wechat-pay/products/lead-plans",
        source_status="next_payment_admin_deprecated",
        replacement="/api/admin/wechat-pay/products/lead-channels",
    )


@router.get("/api/admin/wechat-pay/products/{product_id}")
def get_product(product_id: str) -> dict:
    try:
        return _payment_final_payload(GetProductQuery()(product_id))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/wechat-pay/products/{product_id}/share")
def share_product(product_id: str, request: Request) -> dict:
    try:
        product = GetProductQuery()(product_id)["product"]
    except Exception as exc:
        _raise_http(exc)
    return _payment_final_payload({"ok": True, "share": _share_payload(request, product)})


@router.post("/api/admin/wechat-pay/products/{product_id}/copy")
def copy_product(product_id: str) -> JSONResponse:
    try:
        product = build_commerce_repository().copy_product(product_id)
    except Exception as exc:
        _raise_http(exc)
    return JSONResponse(_payment_final_payload({"ok": True, "product": product}), status_code=201, headers=_payment_final_headers())


@router.get("/api/admin/wechat-pay/products/{product_id}/external-push")
def get_product_external_push(product_id: str) -> dict:
    try:
        return _payment_final_payload({"ok": True, "config": build_commerce_repository().get_external_push_config(product_id)})
    except Exception as exc:
        _raise_http(exc)


@router.put("/api/admin/wechat-pay/products/{product_id}/external-push")
def save_product_external_push(product_id: str, request: Request) -> dict:
    try:
        payload = read_request_json(request)
        config = build_commerce_repository().save_external_push_config(product_id, payload if isinstance(payload, dict) else {})
    except Exception as exc:
        _raise_http(exc)
    return _payment_final_payload({"ok": True, "config": config})


@router.post("/api/admin/wechat-pay/products/{product_id}/external-push/test")
def test_product_external_push(product_id: str) -> JSONResponse:
    try:
        result = send_product_external_push_test(int(product_id))
    except ExternalPushAdminError as exc:
        status_code = 404 if str(exc) == "商品不存在" else 400
        return JSONResponse({"ok": False, "error": str(exc), **_payment_final_side_effects()}, status_code=status_code, headers=_payment_final_headers())
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), **_payment_final_side_effects()}, status_code=400, headers=_payment_final_headers())
    return JSONResponse(
        jsonable_encoder(_payment_final_payload({"ok": True, "result": result}, real_external_call_executed=_EXTERNAL_PUSH_CALL_EXECUTED)),
        headers=_payment_final_headers(real_external_call_executed=_EXTERNAL_PUSH_CALL_EXECUTED),
    )


@router.post("/api/admin/wechat-pay/products")
def create_product(payload: ProductUpsertRequest) -> dict:
    try:
        return _payment_final_payload(UpsertProductCommand()(payload))
    except Exception as exc:
        _raise_http(exc)


@router.put("/api/admin/wechat-pay/products/{product_id}")
def update_product(product_id: str, payload: ProductUpsertRequest) -> dict:
    try:
        return _payment_final_payload(UpsertProductCommand()(payload, product_id))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/wechat-pay/products/{product_id}/enable")
def enable_product(product_id: str) -> dict:
    try:
        return _payment_final_payload(SetProductEnabledCommand()(product_id, enabled=True))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/wechat-pay/products/{product_id}/disable")
def disable_product(product_id: str) -> dict:
    try:
        return _payment_final_payload(SetProductEnabledCommand()(product_id, enabled=False))
    except Exception as exc:
        _raise_http(exc)


@router.delete("/api/admin/wechat-pay/products/{product_id}")
def delete_product(product_id: str) -> dict:
    try:
        return _payment_final_payload(DeleteProductCommand()(product_id))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/products/{page_slug}")
def public_product(page_slug: str) -> dict:
    try:
        return GetPublicProductQuery()(page_slug)
    except Exception as exc:
        _raise_http(exc)


@router.get("/p/{page_slug}", response_class=HTMLResponse)
def product_page(request: Request, page_slug: str) -> str:
    try:
        payload = GetPublicProductQuery()(page_slug)
    except Exception as exc:
        _raise_http(exc)
    product = payload["product"]
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>"
        + product["title"]
        + "</title></head><body><main><h1>"
        + product["title"]
        + "</h1><p>"
        + product.get("description", "")
        + "</p><button>"
        + product.get("buy_button_text", "立即购买")
        + "</button></main></body></html>"
    )


@router.post("/api/checkout/wechat")
def checkout_wechat(payload: CheckoutRequest) -> JSONResponse:
    try:
        result = CheckoutCommand("wechat")(payload)
        return JSONResponse(result, headers=_checkout_order_headers(order_create_executed="local_only"))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/checkout/alipay")
def checkout_alipay(payload: CheckoutRequest) -> JSONResponse:
    try:
        result = CheckoutCommand("alipay")(payload)
        return JSONResponse(result, headers=_checkout_order_headers(order_create_executed="local_only"))
    except Exception as exc:
        _raise_http(exc)


@router.options("/api/checkout/wechat")
def checkout_wechat_options() -> JSONResponse:
    payload = _checkout_order_options_payload("/api/checkout/wechat", ["POST", "OPTIONS"])
    return JSONResponse(payload, headers=_checkout_order_headers())


@router.options("/api/checkout/alipay")
def checkout_alipay_options() -> JSONResponse:
    payload = _checkout_order_options_payload("/api/checkout/alipay", ["POST", "OPTIONS"])
    return JSONResponse(payload, headers=_checkout_order_headers())


@router.api_route("/api/checkout/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def checkout_unknown(path: str, request: Request) -> JSONResponse:
    return _checkout_order_error_payload(
        error_code="checkout_path_removed",
        message="unknown checkout API path is closed in Next; legacy fallback is removed",
        method=request.method,
        path=f"/api/checkout/{path}",
        source_status="next_checkout_not_found",
        status_code=410,
    )


@router.get("/api/orders/{order_no}")
@router.get("/api/orders/{order_no}/status")
def get_order(order_no: str) -> JSONResponse:
    try:
        return JSONResponse(GetOrderQuery()(order_no), headers=_checkout_order_headers())
    except NotFoundError as exc:
        return _checkout_order_error_payload(
            error_code="order_not_found",
            message=str(exc),
            method="GET",
            path=f"/api/orders/{order_no}",
            source_status="next_order_read_not_found",
            status_code=404,
        )
    except Exception as exc:
        _raise_http(exc)


@router.options("/api/orders/{order_no}")
def get_order_options(order_no: str) -> JSONResponse:
    payload = _checkout_order_options_payload(f"/api/orders/{order_no}", ["GET", "OPTIONS"], adapter_mode="none")
    payload["source_status"] = "next_order_read"
    return JSONResponse(payload, headers=_checkout_order_headers())


@router.options("/api/orders/{order_no}/status")
def get_order_status_options(order_no: str) -> JSONResponse:
    payload = _checkout_order_options_payload(f"/api/orders/{order_no}/status", ["GET", "OPTIONS"], adapter_mode="none")
    payload["source_status"] = "next_order_read"
    return JSONResponse(payload, headers=_checkout_order_headers())


@router.api_route("/api/orders/{order_no}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def order_unknown_child(order_no: str, path: str, request: Request) -> JSONResponse:
    return _checkout_order_error_payload(
        error_code="order_child_path_removed",
        message="unknown order API child path is closed in Next; legacy fallback is removed",
        method=request.method,
        path=f"/api/orders/{order_no}/{path}",
        source_status="next_order_child_not_found",
        status_code=410,
    )


@router.post("/api/wechat-pay/notify")
def wechat_notify(payload: PaymentNotifyRequest) -> JSONResponse:
    try:
        result = NotifyPaymentCommand("wechat")(payload)
        return JSONResponse(result, headers=_provider_payment_headers(notify_executed="local_only"))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/alipay/notify")
def alipay_notify(payload: PaymentNotifyRequest) -> JSONResponse:
    try:
        result = NotifyPaymentCommand("alipay")(payload)
        return JSONResponse(result, headers=_provider_payment_headers(notify_executed="local_only"))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/wechat-shop/notify")
def wechat_shop_notify_verify(request: Request) -> Response:
    try:
        text = verify_wechat_shop_echo(dict(request.query_params))
        return Response(text, media_type="text/plain")
    except ValueError as exc:
        return Response(str(exc), media_type="text/plain", status_code=403)


@router.post("/api/wechat-shop/notify")
def wechat_shop_notify(request: Request) -> Response:
    try:
        body = read_request_body(request)
        payload = json.loads(body.decode("utf-8")) if body else {}
        if not isinstance(payload, dict):
            return Response("invalid payload", media_type="text/plain", status_code=400)
        handle_wechat_shop_notify(payload, dict(request.query_params))
        return Response("success", media_type="text/plain")
    except json.JSONDecodeError:
        return Response("invalid payload", media_type="text/plain", status_code=400)
    except ValueError as exc:
        status_code = 403 if "signature" in str(exc).lower() or "callback token" in str(exc).lower() else 400
        return Response(str(exc), media_type="text/plain", status_code=status_code)
    except Exception as exc:
        safe_log_exception(logger, "wechat shop notify failed before durable event handling", exc)
        return Response("wechat shop notify failed", media_type="text/plain", status_code=500)


@router.get("/api/alipay/return")
def alipay_return(order_no: str = "", status: str = "paid") -> JSONResponse:
    try:
        result = PaymentReturnCommand()(order_no=order_no, status=status)
        return JSONResponse(result, headers=_provider_payment_headers(return_executed="fake"))
    except Exception as exc:
        _raise_http(exc)


@router.options("/api/wechat-pay/notify")
def wechat_notify_options() -> JSONResponse:
    payload = _provider_payment_options_payload("/api/wechat-pay/notify", ["POST", "OPTIONS"], source_status="next_payment_notify")
    return JSONResponse(payload, headers=_provider_payment_headers())


@router.options("/api/alipay/notify")
def alipay_notify_options() -> JSONResponse:
    payload = _provider_payment_options_payload("/api/alipay/notify", ["POST", "OPTIONS"], source_status="next_payment_notify")
    return JSONResponse(payload, headers=_provider_payment_headers())


@router.options("/api/alipay/return")
def alipay_return_options() -> JSONResponse:
    payload = _provider_payment_options_payload("/api/alipay/return", ["GET", "OPTIONS"], source_status="next_payment_return")
    return JSONResponse(payload, headers=_provider_payment_headers())


@router.api_route("/api/wechat-pay/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def wechat_pay_unknown(path: str, request: Request) -> JSONResponse:
    return _provider_payment_error_payload(
        error_code="provider_payment_path_removed",
        message="unknown WeChat Pay provider path is closed in Next; legacy fallback is removed",
        method=request.method,
        path=f"/api/wechat-pay/{path}",
        source_status="next_payment_provider_not_found",
        status_code=410,
    )


@router.api_route("/api/alipay/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def alipay_unknown(path: str, request: Request) -> JSONResponse:
    return _provider_payment_error_payload(
        error_code="provider_payment_path_removed",
        message="unknown Alipay provider path is closed in Next; legacy fallback is removed",
        method=request.method,
        path=f"/api/alipay/{path}",
        source_status="next_payment_provider_not_found",
        status_code=410,
    )


def _transaction_filters(
    payment_status: str | None = None,
    product_code: str | None = None,
    mobile: str | None = None,
    external_userid: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    return {
        "payment_status": payment_status,
        "product_code": product_code,
        "mobile": mobile,
        "external_userid": external_userid,
        "date_from": date_from,
        "date_to": date_to,
    }


def _unified_order_filters(**kwargs) -> dict:
    return {key: value for key, value in kwargs.items() if value not in {None, ""}}


@router.get(
    "/api/admin/orders",
    summary="后台统一订单列表",
    description="Session Cookie 后台接口，聚合微信、支付宝和微信小店订单。PostgreSQL 模式使用条件下推的 UNION ALL 与稳定游标分页。",
)
def list_admin_orders(
    provider: str = Query("all", description="支付 provider，可取 all/wechat/alipay/wechat_shop，默认 all"),
    status: str | None = Query(None, description="统一订单状态过滤"),
    payment_status: str | None = Query(None, description="支付状态过滤，兼容 status"),
    product_code: str | None = Query(None, description="商品编码"),
    mobile: str | None = Query(None, description="客户手机号模糊过滤"),
    external_userid: str | None = Query(None, description="企业微信 external_userid"),
    identity: str | None = Query(None, description="身份关键字，兼容 external_userid"),
    transaction_id: str | None = Query(None, description="平台交易号"),
    platform_transaction_no: str | None = Query(None, description="平台交易号别名"),
    order_no: str | None = Query(None, description="商户订单号"),
    out_trade_no: str | None = Query(None, description="商户订单号别名"),
    created_from: str | None = Query(None, description="订单创建开始时间"),
    created_to: str | None = Query(None, description="订单创建结束时间"),
    date_from: str | None = Query(None, description="订单创建开始时间别名"),
    date_to: str | None = Query(None, description="订单创建结束时间别名"),
    limit: int = Query(50, description="分页条数，默认 50，最大 100"),
    offset: int = Query(0, description="分页偏移，默认 0"),
    cursor: str | None = Query(None, description="可选下一页游标；与非零 offset 不能同时使用"),
) -> dict:
    try:
        return _payment_final_payload(
            list_unified_orders(
                provider=provider,
                filters=_unified_order_filters(
                    status=status,
                    payment_status=payment_status,
                    product_code=product_code,
                    mobile=mobile,
                    external_userid=external_userid,
                    identity=identity,
                    transaction_id=transaction_id,
                    platform_transaction_no=platform_transaction_no,
                    order_no=order_no,
                    out_trade_no=out_trade_no,
                    created_from=created_from,
                    created_to=created_to,
                    date_from=date_from,
                    date_to=date_to,
                ),
                limit=limit,
                offset=offset,
                cursor=cursor,
            ),
            source_status="next_admin_orders",
        )
    except ValueError as exc:
        return _admin_api_error(error_code="invalid_request", message=str(exc), source_status="next_admin_orders", status_code=400)


@router.get(
    "/api/admin/orders/{order_no}",
    summary="后台统一订单详情",
    description="按商户订单号或平台交易号查询统一订单详情。provider=auto 时查询微信、支付宝、微信小店；404 返回结构化 not_found。",
)
def get_admin_order(
    order_no: str = PathParam(..., description="商户订单号、订单 ID 或平台交易号"),
    provider: str = Query("auto", description="支付 provider，可取 auto/wechat/alipay/wechat_shop，默认 auto"),
) -> JSONResponse:
    try:
        return JSONResponse(jsonable_encoder(_payment_final_payload(get_unified_order(order_no, provider=provider), source_status="next_admin_order_detail")), headers=_payment_final_headers())
    except Exception:
        return _admin_api_error(error_code="not_found", message="order not found", source_status="next_admin_order_detail", status_code=404)


@router.get(
    "/api/admin/orders/{order_no}/items",
    summary="后台订单行项目",
    description="返回订单商品明细。当前没有独立 order_items 表时，从订单详情派生单行 item；未来可替换为真实行项目表。",
)
def get_admin_order_items(
    order_no: str = PathParam(..., description="商户订单号、订单 ID 或平台交易号"),
    provider: str = Query("auto", description="支付 provider，可取 auto/wechat/alipay/wechat_shop，默认 auto"),
) -> JSONResponse:
    try:
        return JSONResponse(jsonable_encoder(_payment_final_payload(list_unified_order_items(order_no, provider=provider), source_status="next_admin_order_items")), headers=_payment_final_headers())
    except Exception:
        return _admin_api_error(error_code="not_found", message="order not found", source_status="next_admin_order_items", status_code=404)


@router.get(
    "/api/admin/payments",
    summary="后台支付流水列表",
    description="Session Cookie 后台接口，复用统一订单列表逻辑，以支付流水视角返回 payments。",
)
def list_admin_payments(
    provider: str = Query("all", description="支付 provider，可取 all/wechat/alipay/wechat_shop，默认 all"),
    status: str | None = Query(None, description="支付状态过滤"),
    payment_status: str | None = Query(None, description="支付状态过滤别名"),
    product_code: str | None = Query(None, description="商品编码"),
    mobile: str | None = Query(None, description="客户手机号模糊过滤"),
    external_userid: str | None = Query(None, description="企业微信 external_userid"),
    identity: str | None = Query(None, description="身份关键字，兼容 external_userid"),
    transaction_id: str | None = Query(None, description="平台交易号"),
    platform_transaction_no: str | None = Query(None, description="平台交易号别名"),
    order_no: str | None = Query(None, description="商户订单号"),
    out_trade_no: str | None = Query(None, description="商户订单号别名"),
    created_from: str | None = Query(None, description="订单创建开始时间"),
    created_to: str | None = Query(None, description="订单创建结束时间"),
    paid_from: str | None = Query(None, description="支付完成开始时间"),
    paid_to: str | None = Query(None, description="支付完成结束时间"),
    limit: int = Query(50, description="分页条数，默认 50，最大 100"),
    offset: int = Query(0, description="分页偏移，默认 0"),
    cursor: str | None = Query(None, description="可选下一页游标；与非零 offset 不能同时使用"),
) -> dict:
    try:
        return _payment_final_payload(
            list_unified_payments(
                provider=provider,
                filters=_unified_order_filters(
                    status=status,
                    payment_status=payment_status,
                    product_code=product_code,
                    mobile=mobile,
                    external_userid=external_userid,
                    identity=identity,
                    transaction_id=transaction_id,
                    platform_transaction_no=platform_transaction_no,
                    order_no=order_no,
                    out_trade_no=out_trade_no,
                    created_from=created_from,
                    created_to=created_to,
                    paid_from=paid_from,
                    paid_to=paid_to,
                ),
                limit=limit,
                offset=offset,
                cursor=cursor,
            ),
            source_status="next_admin_payments",
        )
    except ValueError as exc:
        return _admin_api_error(error_code="invalid_request", message=str(exc), source_status="next_admin_payments", status_code=400)


@router.get(
    "/api/admin/refunds",
    summary="后台退款列表",
    description="Session Cookie 后台接口。PostgreSQL 模式优先查 wechat_pay_refunds；支付宝退款表不存在时返回空列表和 warnings，不报 500。",
)
def list_admin_refunds(
    provider: str = Query("all", description="退款 provider，可取 all/wechat/alipay，默认 all"),
    order_no: str | None = Query(None, description="商户订单号"),
    out_trade_no: str | None = Query(None, description="商户订单号别名"),
    transaction_id: str | None = Query(None, description="平台交易号"),
    refund_id: str | None = Query(None, description="平台退款单号"),
    out_refund_no: str | None = Query(None, description="商户退款单号"),
    status: str | None = Query(None, description="退款状态"),
    created_from: str | None = Query(None, description="退款创建开始时间"),
    created_to: str | None = Query(None, description="退款创建结束时间"),
    limit: int = Query(50, description="分页条数，默认 50，最大 100"),
    offset: int = Query(0, description="分页偏移，默认 0"),
) -> dict:
    return _payment_final_payload(
        list_unified_refunds(
            provider=provider,
            filters=_unified_order_filters(
                order_no=order_no,
                out_trade_no=out_trade_no,
                transaction_id=transaction_id,
                refund_id=refund_id,
                out_refund_no=out_refund_no,
                status=status,
                created_from=created_from,
                created_to=created_to,
            ),
            limit=limit,
            offset=offset,
        ),
        source_status="next_admin_refunds",
    )


@router.post(
    "/api/admin/refunds",
    summary="后台统一退款申请",
    description="统一退款入口。provider=wechat 复用 create_wechat_refund_request；provider=alipay 在本阶段返回结构化 provider_refund_not_supported。",
)
def create_admin_refund(payload: dict = Body(..., description="退款申请 JSON，包含 provider/order_no/refund_amount_total/reason/transaction_id_confirmation/checked/operator")) -> JSONResponse:
    try:
        return JSONResponse(jsonable_encoder(_payment_final_payload(request_unified_refund(payload), source_status="next_admin_refund_request")), headers=_payment_final_headers())
    except ValueError as exc:
        if str(exc) == "provider_refund_not_supported":
            return _admin_api_error(
                error_code="provider_refund_not_supported",
                message="provider refund is not supported in this slice",
                source_status="next_refund_admin",
                status_code=400,
            )
        return _admin_api_error(error_code="invalid_refund_request", message=str(exc), source_status="next_admin_refund_request", status_code=400)
    except Exception as exc:
        return _admin_api_error(error_code="refund_request_failed", message=str(exc), source_status="next_admin_refund_request", status_code=400)


@router.get(
    "/api/admin/customers/{external_userid}/orders",
    summary="客户订单列表",
    description="按 external_userid 查询客户名下订单，复用统一订单查询逻辑，不返回商业档案其它维度。",
)
def get_admin_customer_orders(
    external_userid: str = PathParam(..., description="企业微信 external_userid"),
    provider: str = Query("all", description="支付 provider，可取 all/wechat/alipay，默认 all"),
    status: str | None = Query(None, description="订单状态"),
    product_code: str | None = Query(None, description="商品编码"),
    limit: int = Query(20, description="分页条数，默认 20，最大 100"),
    offset: int = Query(0, description="分页偏移，默认 0"),
    cursor: str | None = Query(None, description="可选下一页游标；与非零 offset 不能同时使用"),
) -> dict:
    return _payment_final_payload(
        list_customer_orders(
            external_userid,
            provider=provider,
            status=status,
            product_code=product_code,
            limit=limit,
            offset=offset,
            cursor=cursor,
        ),
        source_status="next_customer_orders",
    )


@router.get(
    "/api/admin/customers/{external_userid}/commerce-summary",
    summary="客户商业摘要",
    description="基于客户订单列表计算订单数、支付金额、退款金额和最近商品等摘要，不引入外部依赖。",
)
def get_admin_customer_commerce_summary(
    external_userid: str = PathParam(..., description="企业微信 external_userid"),
    provider: str = Query("all", description="支付 provider，可取 all/wechat/alipay，默认 all"),
) -> dict:
    return _payment_final_payload(customer_commerce_summary(external_userid, provider=provider), source_status="next_customer_commerce_summary")


@router.get(
    "/api/admin/webhooks/events",
    summary="后台 Webhook 事件排障列表",
    description="后台排障接口，不是外部 webhook callback。PostgreSQL 模式读取微信/支付宝支付事件表；表不存在时返回 warnings 而不是 500。",
)
def list_admin_webhook_events(
    source: str = Query("all", description="事件来源，可取 all/wechat-pay/alipay/wecom/customer-automation"),
    event_type: str | None = Query(None, description="事件类型"),
    order_no: str | None = Query(None, description="商户订单号"),
    out_trade_no: str | None = Query(None, description="商户订单号别名"),
    transaction_id: str | None = Query(None, description="平台交易号"),
    status: str | None = Query(None, description="平台状态"),
    created_from: str | None = Query(None, description="事件创建开始时间"),
    created_to: str | None = Query(None, description="事件创建结束时间"),
    limit: int = Query(50, description="分页条数，默认 50，最大 100"),
    offset: int = Query(0, description="分页偏移，默认 0"),
) -> dict:
    return _payment_final_payload(
        list_webhook_events(
            source=source,
            filters=_unified_order_filters(
                event_type=event_type,
                order_no=order_no,
                out_trade_no=out_trade_no,
                transaction_id=transaction_id,
                status=status,
                created_from=created_from,
                created_to=created_to,
            ),
            limit=limit,
            offset=offset,
        ),
        source_status="next_admin_webhook_events",
    )


@router.post(
    "/api/admin/webhooks/replay",
    summary="后台 Webhook 重放排障",
    description="后台排障接口，不是外部 webhook callback。默认 dry_run=true，不产生业务副作用；dry_run=false 如无法安全重放则返回 webhook_replay_not_supported。",
)
def replay_admin_webhook(payload: dict = Body(..., description="Webhook replay JSON，包含 source/event_id/dry_run/operator")) -> JSONResponse:
    try:
        return JSONResponse(jsonable_encoder(_payment_final_payload(replay_webhook(payload), source_status="next_admin_webhook_replay")), headers=_payment_final_headers())
    except RuntimeError:
        return _admin_api_error(
            error_code="webhook_replay_not_supported",
            message="webhook replay is not supported for non dry_run in this slice",
            source_status="next_admin_webhook_replay",
            status_code=400,
        )
    except LookupError as exc:
        return _admin_api_error(error_code="not_found", message=str(exc), source_status="next_admin_webhook_replay", status_code=404)


@router.post("/api/admin/wechat-shop/orders/{order_id}/sync")
def sync_admin_wechat_shop_order(order_id: str = PathParam(..., description="微信小店订单号")) -> JSONResponse:
    try:
        payload = sync_wechat_shop_order(str(order_id))
        return JSONResponse(jsonable_encoder(_payment_final_payload(payload, source_status="next_wechat_shop_order_sync")), headers=_payment_final_headers())
    except Exception as exc:
        return _admin_api_error(
            error_code="wechat_shop_order_sync_failed",
            message=sanitize_wechat_shop_error(exc),
            source_status="next_wechat_shop_order_sync",
            status_code=400,
        )


@router.get("/api/admin/wechat-shop/events")
def list_admin_wechat_shop_events(
    order_id: str | None = Query(None, description="微信小店订单号"),
    limit: int = Query(50, description="分页条数，默认 50，最大 100"),
    offset: int = Query(0, description="分页偏移，默认 0"),
) -> JSONResponse:
    payload = list_wechat_shop_events({"order_id": order_id}, limit=limit, offset=offset)
    return JSONResponse(jsonable_encoder(_payment_final_payload(payload, source_status="next_wechat_shop_events")), headers=_payment_final_headers())


@router.get("/api/admin/wechat-shop/sync-runs")
def list_admin_wechat_shop_sync_runs(
    limit: int = Query(50, description="分页条数，默认 50，最大 100"),
    offset: int = Query(0, description="分页偏移，默认 0"),
) -> JSONResponse:
    payload = list_wechat_shop_sync_runs(limit=limit, offset=offset)
    return JSONResponse(jsonable_encoder(_payment_final_payload(payload, source_status="next_wechat_shop_sync_runs")), headers=_payment_final_headers())


@router.post(
    "/api/admin/exports",
    summary="创建后台导出任务",
    description="创建轻量同步导出任务。本阶段不接外部对象存储；没有持久化 export job 存储时使用模块级内存 store，并标注 next_export_in_memory。",
)
def create_admin_export(
    request: Request,
    payload: dict = Body(..., description="导出任务 JSON，resource 支持 orders/payments/refunds/customer_business_profile，format 支持 csv"),
) -> JSONResponse:
    try:
        result = _payment_final_payload(create_export_job(payload), source_status="next_admin_exports")
        set_pii_audit_result_count(request, infer_pii_result_count(result))
        return JSONResponse(jsonable_encoder(result), headers=_payment_final_headers())
    except ValueError as exc:
        return _admin_api_error(error_code="invalid_export_request", message=str(exc), source_status="next_admin_exports", status_code=400)


@router.get(
    "/api/admin/exports/{job_id}",
    summary="获取后台导出任务结果",
    description="返回导出任务 JSON 结果，包含 content_text 或 content_base64。本阶段不新增单独 download endpoint。",
)
def get_admin_export(
    request: Request,
    job_id: str = PathParam(..., description="导出任务 job_id"),
) -> JSONResponse:
    try:
        result = _payment_final_payload(get_export_job(job_id), source_status="next_admin_export_result")
        content = str(result.get("content_text") or "")
        result_count = max(0, sum(1 for _row in csv.reader(io.StringIO(content))) - 1) if content else 0
        set_pii_audit_result_count(request, result_count)
        return JSONResponse(jsonable_encoder(result), headers=_payment_final_headers())
    except LookupError:
        return _admin_api_error(error_code="not_found", message="export job not found", source_status="next_admin_export_result", status_code=404)


@router.get("/api/admin/wechat-pay/transactions")
def list_wechat_transactions(
    payment_status: str | None = None,
    product_code: str | None = None,
    mobile: str | None = None,
    external_userid: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    return _payment_final_payload(
        CommerceAdminTransactionListReadModel("wechat").execute(
            _transaction_filters(payment_status, product_code, mobile, external_userid, date_from, date_to),
            limit=limit,
            offset=offset,
        )
    )


@router.get("/api/admin/wechat-pay/transactions/{order_no}")
def get_wechat_transaction(order_no: str) -> dict:
    try:
        return _payment_final_payload(CommerceAdminTransactionDetailReadModel("wechat").execute(order_no))
    except Exception as exc:
        _raise_http(exc)


@router.options("/api/admin/wechat-pay/{path:path}")
def admin_wechat_pay_options(path: str) -> JSONResponse:
    payload = _payment_final_options_payload(
        f"/api/admin/wechat-pay/{path}",
        ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        source_status="next_payment_admin",
    )
    return JSONResponse(payload, headers=_payment_final_headers())


@router.api_route("/api/admin/wechat-pay/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
def admin_wechat_pay_unknown(path: str, request: Request) -> JSONResponse:
    return _payment_final_blocked_payload(
        error_code="admin_wechat_pay_path_removed",
        message="unknown admin WeChat Pay path is closed in Next; legacy fallback is removed",
        method=request.method,
        path=f"/api/admin/wechat-pay/{path}",
        source_status="next_payment_admin_not_found",
    )


@router.get("/api/admin/alipay/transactions")
def list_alipay_transactions(
    payment_status: str | None = None,
    product_code: str | None = None,
    mobile: str | None = None,
    external_userid: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    return _payment_final_payload(
        CommerceAdminTransactionListReadModel("alipay").execute(
            _transaction_filters(payment_status, product_code, mobile, external_userid, date_from, date_to),
            limit=limit,
            offset=offset,
        )
    )


@router.get("/api/admin/alipay/transactions/{order_no}")
def get_alipay_transaction(order_no: str) -> dict:
    try:
        return _payment_final_payload(CommerceAdminTransactionDetailReadModel("alipay").execute(order_no))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/alipay/orders")
@router.get("/api/admin/alipay/order-export.csv")
def deprecated_admin_alipay_paths(request: Request) -> JSONResponse:
    return _payment_final_blocked_payload(
        error_code="admin_alipay_path_removed",
        message="legacy Alipay admin order/export path is removed; use Next transaction APIs",
        method=request.method,
        path=request.url.path,
        source_status="next_payment_admin_deprecated",
        replacement="/api/admin/alipay/transactions",
    )


@router.options("/api/admin/alipay/{path:path}")
def admin_alipay_options(path: str) -> JSONResponse:
    payload = _payment_final_options_payload(
        f"/api/admin/alipay/{path}",
        ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        source_status="next_payment_admin",
    )
    return JSONResponse(payload, headers=_payment_final_headers())


@router.api_route("/api/admin/alipay/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
def admin_alipay_unknown(path: str, request: Request) -> JSONResponse:
    return _payment_final_blocked_payload(
        error_code="admin_alipay_path_removed",
        message="unknown admin Alipay path is closed in Next; legacy fallback is removed",
        method=request.method,
        path=f"/api/admin/alipay/{path}",
        source_status="next_payment_admin_not_found",
    )


@router.options("/api/h5/wechat-pay/{path:path}")
def h5_wechat_pay_options(path: str) -> JSONResponse:
    payload = _payment_final_options_payload(
        f"/api/h5/wechat-pay/{path}",
        ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        source_status="next_h5_payment_blocked",
    )
    return JSONResponse(payload, headers=_payment_final_headers())


@router.api_route("/api/h5/wechat-pay/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
def h5_wechat_pay_unknown(path: str, request: Request) -> JSONResponse:
    return _payment_final_blocked_payload(
        error_code="h5_wechat_pay_path_removed",
        message="legacy H5 WeChat Pay path is closed in Next; use public checkout/order/provider APIs",
        method=request.method,
        path=f"/api/h5/wechat-pay/{path}",
        source_status="next_h5_payment_blocked",
        replacement="/api/checkout/wechat",
    )


@router.options("/api/h5/alipay/{path:path}")
def h5_alipay_options(path: str) -> JSONResponse:
    payload = _payment_final_options_payload(
        f"/api/h5/alipay/{path}",
        ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        source_status="next_h5_payment_blocked",
    )
    return JSONResponse(payload, headers=_payment_final_headers())


@router.api_route("/api/h5/alipay/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
def h5_alipay_unknown(path: str, request: Request) -> JSONResponse:
    return _payment_final_blocked_payload(
        error_code="h5_alipay_path_removed",
        message="legacy H5 Alipay path is closed in Next; use public checkout/order/provider APIs",
        method=request.method,
        path=f"/api/h5/alipay/{path}",
        source_status="next_h5_payment_blocked",
        replacement="/api/checkout/alipay",
    )
