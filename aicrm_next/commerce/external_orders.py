from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.identity_contact.application import ResolvePersonIdentityQuery
from aicrm_next.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.shared.errors import NotFoundError

from .admin_unified_orders import get_order, list_orders


router = APIRouter()
ROUTE_OWNER = "ai_crm_next"
SOURCE_STATUS_LIST = "external_orders"
SOURCE_STATUS_DETAIL = "external_order_detail"
SOURCE_STATUS_USER_BASIC = "external_user_basic"
PAID_STATUSES = {"paid", "refund_processing", "partial_refunded", "full_refunded"}
REFUND_STATUSES = {"refund_processing", "partial_refunded", "full_refunded"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _error(*, error_code: str, message: str, status_code: int, source_status: str) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error_code": error_code,
            "message": message,
            "route_owner": ROUTE_OWNER,
            "source_status": source_status,
            "fallback_used": False,
        },
        status_code=status_code,
    )


def _encode_cursor(offset: int | None) -> str:
    if offset is None:
        return ""
    payload = json.dumps({"offset": max(0, int(offset))}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _timestamp_filter(value: int | None, name: str) -> str | None:
    if value is None:
        return None
    if value < 0:
        raise ValueError(f"{name} must be a Unix timestamp in seconds")
    if value > 9_999_999_999:
        raise ValueError(f"{name} must be a Unix timestamp in seconds, not milliseconds")
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _filters(**kwargs: Any) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value not in {None, ""}}


def _status(order: dict[str, Any]) -> str:
    return _text(order.get("status") or order.get("payment_status"))


def _is_paid(order: dict[str, Any]) -> bool:
    return _status(order) in PAID_STATUSES or bool(_text(order.get("paid_at")))


def _is_refunded(order: dict[str, Any]) -> bool:
    return _status(order) in REFUND_STATUSES or _int(order.get("refunded_amount_total")) > 0 or _int(order.get("active_refund_amount_total")) > 0


def _order_no(order: dict[str, Any]) -> str:
    return _text(order.get("order_no") or order.get("out_trade_no") or order.get("merchant_order_no") or order.get("id"))


def _project_order(order: dict[str, Any]) -> dict[str, Any]:
    provider = _text(order.get("provider")) or "wechat"
    order_no = _order_no(order)
    customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}
    payment_status = _status(order)
    return {
        "provider": provider,
        "order_no": order_no,
        "transaction_id": _text(order.get("transaction_id") or order.get("platform_transaction_no")),
        "paid_at": _text(order.get("paid_at")),
        "created_at": _text(order.get("created_at")),
        "product_code": _text(order.get("product_code")),
        "payment_status": payment_status,
        "status_label": _text(order.get("status_label")),
        "amount_total": _int(order.get("amount_total")),
        "amount_yuan": _text(order.get("amount_yuan")),
        "currency": _text(order.get("currency")) or "CNY",
        "is_paid": _is_paid(order),
        "is_refunded": _is_refunded(order),
        "refund_status": _text(order.get("refund_status")),
        "refunded_amount_total": _int(order.get("refunded_amount_total")),
        "mobile": _text(order.get("mobile") or customer.get("mobile")),
        "unionid": _text(order.get("unionid") or customer.get("unionid")),
        "external_userid": _text(order.get("external_userid") or customer.get("external_userid")),
        "detail_url": f"/api/external/orders/{quote(order_no)}?provider={quote(provider)}",
    }


@router.get("/api/external/orders")
def list_external_orders(
    request: Request,
    provider: str = Query("all", description="all/wechat/alipay/wechat_shop"),
    paid_from: int | None = Query(None, description="付款开始秒级 Unix 时间戳"),
    paid_to: int | None = Query(None, description="付款结束秒级 Unix 时间戳"),
    created_from: int | None = Query(None, description="订单创建开始秒级 Unix 时间戳"),
    created_to: int | None = Query(None, description="订单创建结束秒级 Unix 时间戳"),
    product_code: str | None = Query(None, description="商品编码"),
    payment_status: str | None = Query(None, description="订单支付状态"),
    is_paid: str | None = Query(None, description="true/false"),
    is_refunded: str | None = Query(None, description="true/false"),
    order_no: str | None = Query(None, description="商户订单号"),
    transaction_id: str | None = Query(None, description="平台交易号"),
    mobile: str | None = Query(None, description="手机号"),
    external_userid: str | None = Query(None, description="企业微信 external_userid"),
    unionid: str | None = Query(None, description="微信 unionid"),
    limit: int = Query(100, ge=1, le=500, description="分页条数，最大 500"),
    cursor: str | None = Query(None, description="下一页游标"),
) -> JSONResponse:
    try:
        payload = list_orders(
            provider=provider,
            filters=_filters(
                paid_from=_timestamp_filter(paid_from, "paid_from"),
                paid_to=_timestamp_filter(paid_to, "paid_to"),
                created_from=_timestamp_filter(created_from, "created_from"),
                created_to=_timestamp_filter(created_to, "created_to"),
                product_code=product_code,
                payment_status=payment_status,
                is_paid=is_paid,
                is_refunded=is_refunded,
                order_no=order_no,
                transaction_id=transaction_id,
                mobile=mobile,
                external_userid=external_userid,
                unionid=unionid,
            ),
            limit=limit,
            offset=0,
            max_limit=500,
            cursor=cursor,
        )
    except ValueError as exc:
        return _error(error_code="invalid_request", message=str(exc), status_code=400, source_status=SOURCE_STATUS_LIST)
    items = [_project_order(dict(item)) for item in payload.get("items", [])]
    response_payload = {
        "ok": True,
        "items": items,
        "total": int(payload.get("total") or len(items)),
        "limit": int(payload.get("limit") or limit),
        "next_cursor": _text(payload.get("next_cursor")) or _encode_cursor(payload.get("next_offset")),
        "has_more": bool(payload.get("has_more")),
        "filters": payload.get("filters") or {},
        "providers": payload.get("providers") or [],
        "route_owner": ROUTE_OWNER,
        "source_status": SOURCE_STATUS_LIST,
        "fallback_used": False,
    }
    return JSONResponse(jsonable_encoder(response_payload))


@router.get("/api/external/orders/{order_no}")
def get_external_order(
    request: Request,
    order_no: str = Path(..., description="商户订单号、订单 ID 或平台交易号"),
    provider: str = Query("auto", description="auto/wechat/alipay/wechat_shop"),
) -> JSONResponse:
    try:
        payload = get_order(order_no, provider=provider)
    except NotFoundError:
        return _error(error_code="not_found", message="order not found", status_code=404, source_status=SOURCE_STATUS_DETAIL)
    except ValueError as exc:
        return _error(error_code="invalid_request", message=str(exc), status_code=400, source_status=SOURCE_STATUS_DETAIL)
    response_payload = {
        "ok": True,
        "order": _project_detail_order(payload.get("order") or {}),
        "route_owner": ROUTE_OWNER,
        "source_status": SOURCE_STATUS_DETAIL,
        "fallback_used": False,
    }
    return JSONResponse(jsonable_encoder(response_payload))


def _project_detail_order(order: dict[str, Any]) -> dict[str, Any]:
    projected = dict(order)
    projected.pop("product_name", None)
    return projected


def _model_dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return dict(value)


def _customer_detail(request: Request, external_userid: str) -> dict[str, Any]:
    if not external_userid:
        return {}
    query = getattr(request.app.state, "external_customer_detail_query", None)
    if not callable(query):
        return {}
    try:
        payload = query(external_userid)
    except NotFoundError:
        return {}
    if not payload.get("ok"):
        return {}
    return dict(payload.get("customer") or {})


def _project_user_basic(identity: dict[str, Any], customer: dict[str, Any]) -> dict[str, Any]:
    customer_identity = dict(customer.get("identity") or {})
    binding = dict(customer.get("binding") or {})
    external_userid = _text(identity.get("external_userid") or customer.get("external_userid") or customer_identity.get("external_userid"))
    mobile = _text(customer.get("mobile") or binding.get("mobile") or customer_identity.get("mobile") or identity.get("mobile"))
    unionid = _text(identity.get("unionid") or customer_identity.get("unionid") or customer.get("unionid"))
    customer_name = _text(customer.get("customer_name") or customer.get("remark") or customer.get("name"))
    return {
        "person_id": _text(identity.get("person_id") or customer.get("person_id") or customer_identity.get("person_id")),
        "external_userid": external_userid,
        "mobile": mobile,
        "customer_name": customer_name,
        "unionid": unionid,
        "openid": _text(identity.get("openid") or customer_identity.get("openid") or customer.get("openid")),
        "owner_userid": _text(customer.get("owner_userid") or identity.get("owner_userid")),
        "owner_display_name": _text(customer.get("owner_display_name")),
        "remark": _text(customer.get("remark")),
        "follow_user_userid": _text(identity.get("follow_user_userid")),
        "follow_user_userids": [_text(item) for item in list(customer.get("follow_user_userids") or []) if _text(item)],
        "binding_status": _text(customer.get("binding_status") or identity.get("binding_status")),
        "is_bound": bool(customer.get("is_bound") or mobile),
        "matched_by": _text(identity.get("matched_by")),
        "identity_map_id": identity.get("identity_map_id"),
        "detail_url": f"/api/customers/{quote(external_userid)}" if external_userid else "",
    }


def _matched_by_from_request(
    *,
    unionid: str | None = None,
    external_userid: str | None = None,
    mobile: str | None = None,
    openid: str | None = None,
) -> str:
    for key, value in (
        ("unionid", unionid),
        ("external_userid", external_userid),
        ("mobile", mobile),
        ("openid", openid),
    ):
        if _text(value):
            return key
    return ""


@router.get("/api/external/users/resolve")
def resolve_external_user_basic(
    request: Request,
    unionid: str | None = Query(None, description="微信 unionid"),
    external_userid: str | None = Query(None, description="企业微信 external_userid"),
    mobile: str | None = Query(None, description="手机号"),
    openid: str | None = Query(None, description="微信 openid"),
) -> JSONResponse:
    if not any(_text(value) for value in (unionid, external_userid, mobile, openid)):
        return _error(
            error_code="invalid_request",
            message="one of unionid, external_userid, mobile, or openid is required",
            status_code=400,
            source_status=SOURCE_STATUS_USER_BASIC,
        )

    identity = _model_dump(
        ResolvePersonIdentityQuery()(
            ResolvePersonIdentityRequest(
                external_userid=external_userid,
                mobile=mobile,
                openid=openid,
                unionid=unionid,
            )
        )
    )
    if not _text(identity.get("matched_by")):
        identity["matched_by"] = _matched_by_from_request(
            unionid=unionid,
            external_userid=external_userid,
            mobile=mobile,
            openid=openid,
        )
    customer = _customer_detail(request, _text(identity.get("external_userid") or external_userid))
    if not identity and not customer:
        return _error(error_code="not_found", message="user not found", status_code=404, source_status=SOURCE_STATUS_USER_BASIC)

    response_payload = {
        "ok": True,
        "user": _project_user_basic(identity, customer),
        "route_owner": ROUTE_OWNER,
        "source_status": SOURCE_STATUS_USER_BASIC,
        "fallback_used": False,
    }
    return JSONResponse(jsonable_encoder(response_payload))
