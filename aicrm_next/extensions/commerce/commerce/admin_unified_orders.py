from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aicrm_next.shared.errors import NotFoundError
from aicrm_next.shared.runtime import database_mode

from .admin_transaction_detail import (
    CommerceAdminTransactionDetailReadModel,
    CommerceAdminTransactionListReadModel,
    CommerceAdminUnifiedTransactionListReadModel,
    UnifiedOrderPageCursor,
    decode_unified_order_cursor,
    unified_order_cursor_scope,
)

ROUTE_OWNER = "ai_crm_next"
PROVIDERS = ("wechat", "alipay", "wechat_shop")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _money_yuan(value: Any) -> str:
    return f"{_int(value) / 100:.2f}"


def _parse_time(value: Any) -> datetime:
    text = _text(value).replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    return datetime.min


def normalize_provider(provider: str | None, *, default: str = "all") -> str:
    normalized = _text(provider or default).lower()
    if normalized in {"", "auto", "all", "wechat", "alipay", "wechat_shop"}:
        return normalized or default
    raise ValueError("provider must be one of all/auto/wechat/alipay/wechat_shop")


def normalize_limit(limit: Any, *, default: int = 50, maximum: int = 100) -> int:
    return max(1, min(_int(limit, default), maximum))


def normalize_offset(offset: Any) -> int:
    return max(0, _int(offset, 0))


def normalize_order_filters(filters: dict[str, Any] | None = None) -> dict[str, str]:
    payload = dict(filters or {})
    return {
        "status": _text(payload.get("status") or payload.get("payment_status")),
        "payment_status": _text(payload.get("payment_status") or payload.get("status")),
        "product_code": _text(payload.get("product_code")),
        "product_name": _text(payload.get("product_name")),
        "mobile": _text(payload.get("mobile")),
        "external_userid": _text(payload.get("external_userid") or payload.get("identity")),
        "identity": _text(payload.get("identity") or payload.get("external_userid")),
        "unionid": _text(payload.get("unionid")),
        "transaction_id": _text(payload.get("transaction_id") or payload.get("platform_transaction_no")),
        "platform_transaction_no": _text(payload.get("platform_transaction_no") or payload.get("transaction_id")),
        "order_no": _text(payload.get("order_no") or payload.get("out_trade_no")),
        "out_trade_no": _text(payload.get("out_trade_no") or payload.get("order_no")),
        "created_from": _text(payload.get("created_from") or payload.get("date_from")),
        "created_to": _text(payload.get("created_to") or payload.get("date_to")),
        "date_from": _text(payload.get("date_from") or payload.get("created_from")),
        "date_to": _text(payload.get("date_to") or payload.get("created_to")),
        "paid_from": _text(payload.get("paid_from")),
        "paid_to": _text(payload.get("paid_to")),
        "is_paid": _text(payload.get("is_paid")).lower(),
        "is_refunded": _text(payload.get("is_refunded")).lower(),
    }


def _normalize_order_item(item: dict[str, Any]) -> dict[str, Any]:
    order = dict(item)
    order_no = _text(order.get("order_no") or order.get("out_trade_no") or order.get("merchant_order_no"))
    order["order_no"] = order_no
    order["out_trade_no"] = order_no
    order.setdefault("merchant_order_no", order_no)
    order.setdefault("platform_transaction_no", _text(order.get("transaction_id")))
    order.setdefault("transaction_id", _text(order.get("platform_transaction_no")))
    customer = dict(order.get("customer") or {})
    customer.setdefault("name", _text(order.get("payer_name")))
    customer.setdefault("mobile", _text(order.get("mobile")))
    customer.setdefault("external_userid", _text(order.get("external_userid")))
    customer.setdefault("userid", _text(order.get("userid")))
    customer.setdefault("unionid", _text(order.get("unionid")))
    order["customer"] = customer
    order.setdefault("currency", "CNY")
    order.setdefault("amount_yuan", _money_yuan(order.get("amount_total")))
    order.setdefault("refunded_amount_total", 0)
    order.setdefault("refundable_amount_total", max(0, _int(order.get("amount_total")) - _int(order.get("refunded_amount_total"))))
    order.setdefault("can_refund", False)
    return order


_PAID_STATUSES = {"paid", "refund_processing", "partial_refunded", "full_refunded"}
_REFUND_STATUSES = {"refund_processing", "partial_refunded", "full_refunded"}
_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}


def _bool_filter(value: str) -> bool | None:
    normalized = _text(value).lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def _order_status(item: dict[str, Any]) -> str:
    return _text(item.get("status") or item.get("payment_status")).lower()


def _is_paid(item: dict[str, Any]) -> bool:
    return _order_status(item) in _PAID_STATUSES or bool(_text(item.get("paid_at")))


def _is_refunded(item: dict[str, Any]) -> bool:
    return (
        _order_status(item) in _REFUND_STATUSES
        or _int(item.get("refunded_amount_total")) > 0
        or _int(item.get("active_refund_amount_total")) > 0
    )


def _post_filter(items: list[dict[str, Any]], filters: dict[str, str]) -> list[dict[str, Any]]:
    order_no = filters.get("order_no") or filters.get("out_trade_no")
    paid_from = filters.get("paid_from")
    paid_to = filters.get("paid_to")
    rows = items
    if order_no:
        rows = [item for item in rows if order_no in _text(item.get("order_no") or item.get("out_trade_no") or item.get("merchant_order_no"))]
    if filters.get("product_name"):
        needle = filters["product_name"].lower()
        rows = [item for item in rows if needle in _text(item.get("product_name") or item.get("product_title")).lower()]
    if filters.get("unionid"):
        needle = filters["unionid"].lower()
        rows = [item for item in rows if needle in _text(item.get("unionid") or (item.get("customer") or {}).get("unionid")).lower()]
    if paid_from:
        rows = [item for item in rows if _text(item.get("paid_at")) and _parse_time(item.get("paid_at")) >= _parse_time(paid_from)]
    if paid_to:
        rows = [item for item in rows if _text(item.get("paid_at")) and _parse_time(item.get("paid_at")) <= _parse_time(paid_to)]
    paid_filter = _bool_filter(filters.get("is_paid", ""))
    if paid_filter is not None:
        rows = [item for item in rows if _is_paid(item) is paid_filter]
    refund_filter = _bool_filter(filters.get("is_refunded", ""))
    if refund_filter is not None:
        rows = [item for item in rows if _is_refunded(item) is refund_filter]
    return rows


def _order_sort_key(item: dict[str, Any]) -> tuple[datetime, datetime, str]:
    return (
        _parse_time(item.get("created_at")),
        _parse_time(item.get("paid_at")),
        _text(item.get("order_no") or item.get("out_trade_no") or item.get("merchant_order_no")),
    )


def list_orders(
    *,
    provider: str = "all",
    filters: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
    max_limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    normalized_provider = normalize_provider(provider)
    if normalized_provider == "auto":
        normalized_provider = "all"
    page_limit = normalize_limit(limit, maximum=max_limit)
    page_offset = normalize_offset(offset)
    normalized_filters = normalize_order_filters(filters)
    selected = list(PROVIDERS if normalized_provider == "all" else (normalized_provider,))
    if cursor and page_offset:
        raise ValueError("cursor and offset cannot be used together")
    if database_mode() == "postgres":
        payload = CommerceAdminUnifiedTransactionListReadModel().execute(
            selected,
            normalized_filters,
            limit=page_limit,
            offset=page_offset,
            cursor=cursor,
            maximum=max_limit,
        )
        items = [_normalize_order_item(dict(item)) for item in payload.get("items", [])]
        return {
            "ok": True,
            "items": items,
            "total": int(payload.get("total") or 0),
            "limit": int(payload.get("limit") or page_limit),
            "offset": int(payload.get("offset") or 0),
            "has_more": bool(payload.get("has_more")),
            "next_offset": payload.get("next_offset"),
            "next_cursor": _text(payload.get("next_cursor")),
            "filters": normalized_filters,
            "providers": selected,
            "route_owner": ROUTE_OWNER,
            "source_status": "next_admin_orders",
            "fallback_used": False,
        }
    if cursor:
        decoded = decode_unified_order_cursor(
            cursor,
            expected_scope=unified_order_cursor_scope(selected, normalized_filters),
        )
        if isinstance(decoded, UnifiedOrderPageCursor):
            raise ValueError("cursor is invalid")
        page_offset = int(decoded or 0)
    query_limit = page_limit + page_offset if normalized_provider == "all" else page_limit
    items: list[dict[str, Any]] = []
    total = 0
    for provider_name in selected:
        payload = CommerceAdminTransactionListReadModel(provider_name).execute(
            normalized_filters,
            limit=query_limit,
            offset=0 if normalized_provider == "all" else page_offset,
        )
        payload_items = list(payload.get("items", []))
        provider_items = [_normalize_order_item(dict(item)) for item in payload.get("items", [])]
        provider_items = _post_filter(provider_items, normalized_filters)
        items.extend(provider_items)
        provider_total = int(payload.get("total") or len(provider_items))
        if len(provider_items) != len(payload_items):
            provider_total = len(provider_items)
        total += provider_total
    if normalized_provider == "all":
        items.sort(key=_order_sort_key, reverse=True)
        items = items[page_offset : page_offset + page_limit]
    has_more = page_offset + page_limit < total
    return {
        "ok": True,
        "items": items,
        "total": total,
        "limit": page_limit,
        "offset": page_offset,
        "has_more": has_more,
        "next_offset": page_offset + page_limit if has_more else None,
        "next_cursor": "",
        "filters": normalized_filters,
        "providers": selected,
        "route_owner": ROUTE_OWNER,
        "source_status": "next_admin_orders",
        "fallback_used": False,
    }


def get_order(order_no: str, *, provider: str = "auto") -> dict[str, Any]:
    normalized_provider = normalize_provider(provider, default="auto")
    selected = PROVIDERS if normalized_provider == "auto" else (normalized_provider,)
    last_error: Exception | None = None
    for provider_name in selected:
        try:
            payload = CommerceAdminTransactionDetailReadModel(provider_name).execute(order_no)
            return {
                "ok": True,
                "order": _normalize_order_item(dict(payload.get("transaction") or {})),
                "route_owner": ROUTE_OWNER,
                "source_status": "next_admin_order_detail",
                "fallback_used": False,
            }
        except Exception as exc:
            last_error = exc
    raise NotFoundError(str(last_error) if last_error else "order not found")


def list_order_items(order_no: str, *, provider: str = "auto") -> dict[str, Any]:
    detail = get_order(order_no, provider=provider)
    order = dict(detail["order"])
    amount_total = _int(order.get("amount_total"))
    item = {
        "order_no": order.get("order_no") or order.get("out_trade_no") or order_no,
        "provider": order.get("provider"),
        "product_code": order.get("product_code"),
        "product_name": order.get("product_name"),
        "quantity": _int(order.get("quantity"), 1) or 1,
        "unit_amount_total": amount_total,
        "amount_total": amount_total,
        "currency": order.get("currency") or "CNY",
    }
    return {
        "ok": True,
        "order_no": item["order_no"],
        "items": [item],
        "total": 1,
        "route_owner": ROUTE_OWNER,
        "source_status": "next_admin_order_items",
        "fallback_used": False,
    }


def list_payments(
    *,
    provider: str = "all",
    filters: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
    cursor: str | None = None,
) -> dict[str, Any]:
    orders = list_orders(
        provider=provider,
        filters=filters,
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    payments = []
    for order in orders["items"]:
        payments.append(
            {
                "provider": order.get("provider"),
                "provider_label": order.get("provider_label"),
                "order_no": order.get("order_no") or order.get("out_trade_no"),
                "out_trade_no": order.get("out_trade_no") or order.get("order_no"),
                "transaction_id": order.get("transaction_id"),
                "platform_transaction_no": order.get("platform_transaction_no"),
                "amount_total": order.get("amount_total"),
                "currency": order.get("currency"),
                "mobile": order.get("mobile"),
                "payment_status": order.get("status"),
                "status": order.get("status"),
                "paid_at": order.get("paid_at"),
                "customer": order.get("customer") or {},
                "raw_status": order.get("raw_status"),
                "provider_status": order.get("provider_status"),
            }
        )
    return {
        "ok": True,
        "payments": payments,
        "total": orders["total"],
        "limit": orders["limit"],
        "offset": orders["offset"],
        "has_more": orders["has_more"],
        "next_offset": orders["next_offset"],
        "next_cursor": orders.get("next_cursor") or "",
        "filters": orders["filters"],
        "providers": orders["providers"],
        "route_owner": ROUTE_OWNER,
        "source_status": "next_admin_payments",
        "fallback_used": False,
    }


def list_customer_orders(
    external_userid: str,
    *,
    provider: str = "all",
    status: str | None = None,
    product_code: str | None = None,
    limit: int = 20,
    offset: int = 0,
    cursor: str | None = None,
) -> dict[str, Any]:
    payload = list_orders(
        provider=provider,
        filters={"external_userid": external_userid, "status": status, "product_code": product_code},
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    return {
        "ok": True,
        "external_userid": external_userid,
        "orders": payload["items"],
        "total": payload["total"],
        "limit": payload["limit"],
        "offset": payload["offset"],
        "has_more": payload["has_more"],
        "next_offset": payload["next_offset"],
        "next_cursor": payload.get("next_cursor") or "",
        "route_owner": ROUTE_OWNER,
        "source_status": "next_customer_orders",
        "fallback_used": False,
    }


def customer_commerce_summary(external_userid: str, *, provider: str = "all") -> dict[str, Any]:
    payload = list_orders(provider=provider, filters={"external_userid": external_userid}, limit=100, offset=0)
    orders = payload["items"]
    paid_statuses = {"paid", "partial_refunded", "full_refunded", "refund_processing"}
    paid_orders = [item for item in orders if _text(item.get("status")) in paid_statuses]
    latest_order = max(orders, key=lambda item: _parse_time(item.get("created_at")), default={})
    latest_paid = max(paid_orders, key=lambda item: _parse_time(item.get("paid_at")), default={})
    refunded = sum(_int(item.get("refunded_amount_total")) for item in orders)
    total_paid = sum(_int(item.get("amount_total")) for item in paid_orders)
    summary = {
        "order_count": len(orders),
        "paid_order_count": len(paid_orders),
        "total_paid_amount": total_paid,
        "total_paid_amount_yuan": _money_yuan(total_paid),
        "refunded_amount_total": refunded,
        "refunded_amount_yuan": _money_yuan(refunded),
        "latest_order_at": latest_order.get("created_at") or "",
        "latest_paid_at": latest_paid.get("paid_at") or "",
        "latest_product_code": latest_order.get("product_code") or "",
        "latest_product_name": latest_order.get("product_name") or "",
        "providers": sorted({str(item.get("provider")) for item in orders if item.get("provider")}),
    }
    return {
        "ok": True,
        "external_userid": external_userid,
        "summary": summary,
        "route_owner": ROUTE_OWNER,
        "source_status": "next_customer_commerce_summary",
        "fallback_used": False,
    }
