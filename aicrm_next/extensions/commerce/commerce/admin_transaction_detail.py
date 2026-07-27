from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo

from aicrm_next.shared.errors import NotFoundError
from aicrm_next.shared.runtime import database_mode
from aicrm_next.shared.text_encoding import repair_utf8_mojibake

from .application import GetTransactionQuery, ListTransactionsQuery
from .order_expiration import close_expired_wechat_pay_orders
from .product_code_aliases import canonical_product_code, canonical_product_name, product_code_filter_values
from .refund_status import active_wechat_refund_sql
from .repo import connect_commerce_db
from .wechat_shop_service import fixture_wechat_shop_order, fixture_wechat_shop_orders

ADMIN_TZ = ZoneInfo("Asia/Shanghai")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _format_time(value: Any) -> str:
    if isinstance(value, datetime):
        source = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return source.astimezone(ADMIN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return _text(value)


def _money_yuan(value: Any) -> str:
    return f"{_int(value) / 100:.2f}"


class PaymentProviderStatusMapper:
    LABELS = {
        "pending": "待支付",
        "paid": "已支付",
        "closed": "已关闭",
        "refund_processing": "退款处理中",
        "partial_refunded": "部分退款",
        "full_refunded": "全额退款",
        "failed": "支付失败",
    }

    def map(self, provider: str, row: dict[str, Any]) -> dict[str, str]:
        status = self._status(provider, row)
        if provider == "wechat_shop":
            labels = {
                **self.LABELS,
                "paid": "成交",
                "refund_processing": "退货中",
                "partial_refunded": "部分退货",
                "full_refunded": "已退货",
            }
            return {"status": status, "status_label": labels[status]}
        return {"status": status, "status_label": self.LABELS[status]}

    def _status(self, provider: str, row: dict[str, Any]) -> str:
        amount_total = _int(row.get("amount_total") or row.get("amount_cents"))
        refunded = _int(row.get("refunded_amount_total"))
        active_refunding = _int(row.get("active_refund_amount_total"))
        refund_status = _text(row.get("refund_status")).lower()
        raw_status = _text(row.get("status") or row.get("payment_status")).lower()
        provider_state = _text(row.get("trade_state") or row.get("trade_status")).upper()
        if provider == "wechat_shop":
            business_status = _text(row.get("business_status")).lower()
            status_code = _int(row.get("status_code"))
            if row.get("returned_recorded") is True or business_status == "returned":
                if amount_total > 0 and refunded > 0 and refunded < amount_total:
                    return "partial_refunded"
                return "full_refunded"
            if active_refunding > 0 or _int(row.get("on_aftersale_order_count")) > 0:
                return "refund_processing"
            if row.get("deal_recorded") is True or business_status == "deal":
                return "paid"
            if status_code == 250 or business_status == "closed":
                return "closed"
            return "pending"
        if refund_status == "full_refunded" or (amount_total > 0 and refunded >= amount_total):
            return "full_refunded"
        if active_refunding > 0 or refund_status in {"requested", "processing", "refund_processing"}:
            return "refund_processing"
        if refund_status == "partial_refunded" or refunded > 0:
            return "partial_refunded"
        if provider == "alipay":
            if provider_state in {"TRADE_SUCCESS", "TRADE_FINISHED"} or raw_status in {"paid", "success"}:
                return "paid"
            if provider_state == "TRADE_CLOSED" or raw_status in {"closed", "cancelled", "canceled"}:
                return "closed"
            if raw_status in {"failed", "error"}:
                return "failed"
            return "pending"
        if provider_state == "SUCCESS" or raw_status == "paid":
            return "paid"
        if provider_state in {"CLOSED", "REVOKED", "PAYERROR"} or raw_status in {"closed", "cancelled", "canceled"}:
            return "closed"
        if raw_status == "failed":
            return "failed"
        return "pending"


class PaymentTimelineProjection:
    def project(self, *, provider: str, order: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
        timeline: list[dict[str, str]] = []
        if _text(order.get("created_at")):
            timeline.append({"time": _format_time(order.get("created_at")), "event": "订单创建", "status": _text(order.get("status") or order.get("payment_status"))})
        if _text(order.get("paid_at")):
            timeline.append({"time": _format_time(order.get("paid_at")), "event": "支付成功", "status": _text(order.get("trade_state") or order.get("trade_status") or "paid")})
        for event in events[:20]:
            event_type = _text(event.get("event_type")) or "payment_event"
            provider_state = _text(event.get("trade_state") or event.get("trade_status"))
            timeline.append({"time": _format_time(event.get("created_at")), "event": event_type, "status": provider_state})
        latest_event = events[0] if events else {}
        notify_payload = _json_dict(order.get("notify_payload_json"))
        return_payload = _json_dict(order.get("return_payload_json"))
        callback_payload = return_payload if provider == "alipay" and not notify_payload else notify_payload
        notify_payload_present = bool(notify_payload) or bool(order.get("notify_payload_present"))
        return_payload_present = bool(return_payload) or bool(order.get("return_payload_present"))
        projected_payload_keys = order.get("callback_payload_keys")
        payload_keys = (
            sorted(_text(item) for item in projected_payload_keys if _text(item))[:12]
            if isinstance(projected_payload_keys, list)
            else sorted(callback_payload.keys())[:12]
        )
        callback_summary = {
            "event_count": len(events),
            "latest_event_type": _text(latest_event.get("event_type")) or "-",
            "latest_provider_status": _text(latest_event.get("trade_state") or latest_event.get("trade_status")) or "-",
            "notify_payload_present": notify_payload_present,
            "return_payload_present": return_payload_present,
            "payload_keys": payload_keys,
        }
        return {"timeline": timeline, "callback_summary": callback_summary}


@dataclass(frozen=True)
class _ProviderConfig:
    provider: str
    provider_label: str
    platform_no_label: str
    page_path: str
    api_path: str


PROVIDERS = {
    "wechat": _ProviderConfig("wechat", "微信支付", "微信单号", "/admin/wechat-pay/transactions", "/api/admin/wechat-pay/transactions"),
    "alipay": _ProviderConfig("alipay", "支付宝", "支付宝交易号", "/admin/alipay/transactions", "/api/admin/alipay/transactions"),
    "wechat_shop": _ProviderConfig("wechat_shop", "微信小店", "小店订单号", "/admin/wechat-shop/transactions", "/api/admin/orders?provider=wechat_shop"),
}
PROVIDER_SORT_RANK = {"wechat": 3, "alipay": 2, "wechat_shop": 1}


@dataclass(frozen=True)
class UnifiedOrderPageCursor:
    created_at: datetime
    provider_rank: int
    source_id: int
    position: int
    scope: str


def unified_order_cursor_scope(providers: list[str] | tuple[str, ...], filters: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "providers": list(providers),
            "filters": {str(key): _text(value) for key, value in sorted(filters.items()) if _text(value)},
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cursor_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def encode_unified_order_cursor(
    *,
    created_at: Any,
    provider_rank: int,
    source_id: int,
    position: int,
    scope: str,
) -> str:
    timestamp = _cursor_datetime(created_at).isoformat().replace("+00:00", "Z")
    payload = json.dumps(
        {
            "v": 1,
            "created_at": timestamp,
            "provider_rank": int(provider_rank),
            "source_id": int(source_id),
            "position": max(0, int(position)),
            "scope": _text(scope),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_unified_order_cursor(
    cursor: str | None,
    *,
    expected_scope: str,
) -> UnifiedOrderPageCursor | int | None:
    token = _text(cursor)
    if not token:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        if "v" not in payload and "offset" in payload:
            return max(0, int(payload.get("offset") or 0))
        if int(payload.get("v") or 0) != 1:
            raise ValueError
        scope = _text(payload.get("scope"))
        if not scope or scope != expected_scope:
            raise ValueError
        provider_rank = int(payload.get("provider_rank") or 0)
        source_id = int(payload.get("source_id") or 0)
        position = int(payload.get("position") or 0)
        if provider_rank not in PROVIDER_SORT_RANK.values() or source_id <= 0 or position < 0:
            raise ValueError
        return UnifiedOrderPageCursor(
            created_at=_cursor_datetime(payload.get("created_at")),
            provider_rank=provider_rank,
            source_id=source_id,
            position=position,
            scope=scope,
        )
    except Exception as exc:
        raise ValueError("cursor is invalid") from exc

WECHAT_SHOP_BUYER_MOBILE_SQL = (
    "COALESCE("
    "o.raw_order_json #>> '{order,order_detail,delivery_info,address_info,virtual_order_tel_number}', "
    "o.raw_order_json #>> '{order,order_detail,delivery_info,address_info,purchaser_tel_number}', "
    "o.raw_order_json #>> '{order,order_detail,delivery_info,address_info,tel_number}', "
    "o.raw_order_json #>> '{order_detail,delivery_info,address_info,virtual_order_tel_number}', "
    "o.raw_order_json #>> '{order_detail,delivery_info,address_info,purchaser_tel_number}', "
    "o.raw_order_json #>> '{order_detail,delivery_info,address_info,tel_number}', "
    "''"
    ")"
)

WECHAT_SHOP_OPENID_SQL = (
    "COALESCE("
    "o.raw_order_json #>> '{order,openid}', "
    "o.raw_order_json #>> '{order,order_detail,openid}', "
    "o.raw_order_json #>> '{order,order_detail,pay_info,openid}', "
    "o.raw_order_json #>> '{openid}', "
    "o.raw_order_json #>> '{order_detail,openid}', "
    "o.raw_order_json #>> '{order_detail,pay_info,openid}', "
    "''"
    ")"
)


def provider_config(provider: str) -> _ProviderConfig:
    key = _text(provider) or "wechat"
    if key not in PROVIDERS:
        raise NotFoundError("payment provider not found")
    return PROVIDERS[key]


def _platform_transaction_no(provider: str, row: dict[str, Any]) -> str:
    if provider == "wechat_shop":
        return _text(row.get("transaction_id")) or "暂无微信小店支付单号"
    if provider == "alipay":
        return _text(row.get("trade_no") or row.get("transaction_id")) or "待支付暂无支付宝交易号"
    return _text(row.get("transaction_id")) or "待支付暂无微信单号"


def _merchant_order_no(row: dict[str, Any]) -> str:
    return _text(row.get("out_trade_no") or row.get("order_no") or row.get("order_id"))


def _product_name(row: dict[str, Any]) -> str:
    product_code = canonical_product_code(row.get("product_code"))
    product_name = canonical_product_name(product_code, row.get("product_name") or row.get("product_title"))
    return product_name or product_code or "-"


def _customer(row: dict[str, Any]) -> dict[str, str]:
    return {
        "name": repair_utf8_mojibake(row.get("payer_name_snapshot") or row.get("payer_name") or row.get("buyer_logon_id"))
        or "未记录付款人",
        "mobile": _text(row.get("mobile_snapshot") or row.get("buyer_mobile")),
        "userid": _text(row.get("userid_snapshot") or row.get("buyer_id")),
        "external_userid": _text(row.get("external_userid") or row.get("identity_snapshot")),
        "openid": _text(row.get("openid")),
        "unionid": _text(row.get("unionid")),
    }


def _present(provider: str, row: dict[str, Any], *, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    config = provider_config(provider)
    status = PaymentProviderStatusMapper().map(provider, row)
    amount_total = _int(row.get("amount_total") or row.get("amount_cents"))
    refunded = max(0, _int(row.get("refunded_amount_total")))
    active_refunding = max(0, _int(row.get("active_refund_amount_total")))
    refundable = max(0, amount_total - refunded - active_refunding)
    merchant_order_no = _merchant_order_no(row)
    platform_no = _platform_transaction_no(provider, row)
    order_id = merchant_order_no if provider == "wechat_shop" else _text(row.get("id") or row.get("order_no") or merchant_order_no)
    customer = _customer(row)
    product_code = canonical_product_code(row.get("product_code"))
    timeline = PaymentTimelineProjection().project(provider=provider, order=row, events=events or [])
    can_refund = provider in {"wechat", "wechat_shop"} and status["status"] in {"paid", "partial_refunded"} and refundable > 0
    raw_status = _text(row.get("business_status") or row.get("status") or row.get("payment_status")) if provider == "wechat_shop" else _text(row.get("status") or row.get("payment_status"))
    provider_status = _text(row.get("status_code")) if provider == "wechat_shop" else _text(row.get("trade_state") or row.get("trade_status"))
    result = {
        "id": order_id,
        "provider": provider,
        "provider_label": config.provider_label,
        "merchant_order_no": merchant_order_no,
        "out_trade_no": merchant_order_no,
        "platform_transaction_no": platform_no,
        "transaction_id": platform_no,
        "has_transaction_id": bool(_text(row.get("transaction_id") or row.get("trade_no"))),
        "created_at": _format_time(row.get("created_at")),
        "paid_at": _format_time(row.get("paid_at")),
        "customer": customer,
        "payer_name": customer["name"],
        "mobile": customer["mobile"],
        "userid": customer["userid"],
        "external_userid": customer["external_userid"],
        "openid": customer["openid"],
        "unionid": customer["unionid"],
        "product_code": product_code,
        "product_name": _product_name(row),
        "source_product_name": _text(row.get("product_name") or row.get("product_title")),
        "quantity": _int(row.get("product_count")) or _int(row.get("quantity")) or 1,
        "amount_total": amount_total,
        "amount_yuan": _money_yuan(amount_total),
        "currency": _text(row.get("currency")) or "CNY",
        **status,
        "raw_status": raw_status,
        "provider_status": provider_status,
        "refund_status": _text(row.get("refund_status")),
        "refunded_amount_total": refunded,
        "refunded_amount_yuan": _money_yuan(refunded),
        "active_refund_amount_total": active_refunding,
        "active_refund_amount_yuan": _money_yuan(active_refunding),
        "refundable_amount_total": refundable,
        "refundable_amount_yuan": _money_yuan(refundable),
        "can_refund": can_refund,
        "callback_summary": timeline["callback_summary"],
        "timeline": timeline["timeline"],
        "detail_url": f"{config.page_path}/{order_id}",
    }
    if provider == "wechat_shop":
        result["deal_recorded"] = bool(row.get("deal_recorded"))
        result["returned_recorded"] = bool(row.get("returned_recorded"))
    return result


def _connect():
    return connect_commerce_db()


def _identity_lookup_exists_sql(table_alias: str = "o") -> str:
    return f"""
        EXISTS (
            SELECT 1
            FROM crm_user_identity identity
            WHERE identity.unionid = {table_alias}.unionid
              AND (
                identity.primary_external_userid ILIKE %s
                OR identity.primary_openid ILIKE %s
                OR identity.mobile ILIKE %s
                OR identity.mobile_normalized ILIKE %s
              )
        )
    """


def _identity_mobile_exists_sql(table_alias: str = "o") -> str:
    return f"""
        EXISTS (
            SELECT 1
            FROM crm_user_identity identity
            WHERE identity.unionid = {table_alias}.unionid
              AND (identity.mobile ILIKE %s OR identity.mobile_normalized ILIKE %s)
        )
    """


def _postgres_filter_clause(provider: str, filters: dict[str, Any], params: list[Any]) -> str:
    table_alias = "o"
    where = ["1 = 1"]
    product_code = _text(filters.get("product_code"))
    if product_code:
        values = product_code_filter_values(product_code)
        where.append(f"{table_alias}.product_code IN ({', '.join(['%s'] * len(values))})")
        params.extend(values)
    product_name = _text(filters.get("product_name"))
    if product_name:
        where.append(f"COALESCE({table_alias}.product_name, '') ILIKE %s")
        params.append(f"%{product_name}%")
    mobile = _text(filters.get("mobile") or filters.get("mobile_snapshot"))
    if mobile:
        if provider == "wechat_shop":
            where.append(f"{WECHAT_SHOP_BUYER_MOBILE_SQL} ILIKE %s")
            params.append(f"%{mobile}%")
        else:
            where.append(_identity_mobile_exists_sql(table_alias))
            needle = f"%{mobile}%"
            params.extend([needle, needle])
    unionid = _text(filters.get("unionid"))
    if unionid:
        where.append(f"COALESCE({table_alias}.unionid, '') ILIKE %s")
        params.append(f"%{unionid}%")
    identity = _text(filters.get("identity") or filters.get("external_userid"))
    if identity:
        if provider == "wechat_shop":
            where.append(f"({WECHAT_SHOP_OPENID_SQL} ILIKE %s OR COALESCE(o.unionid, '') ILIKE %s)")
            needle = f"%{identity}%"
            params.extend([needle, needle])
        elif provider == "alipay":
            where.append(f"(COALESCE(o.unionid, '') ILIKE %s OR COALESCE(o.buyer_logon_id, '') ILIKE %s OR {_identity_lookup_exists_sql('o')})")
            needle = f"%{identity}%"
            params.extend([needle, needle, needle, needle, needle, needle])
        else:
            where.append(f"(COALESCE(o.unionid, '') ILIKE %s OR {_identity_lookup_exists_sql('o')})")
            needle = f"%{identity}%"
            params.extend([needle, needle, needle, needle, needle])
    transaction = _text(filters.get("transaction_id") or filters.get("platform_transaction_no"))
    if transaction:
        column = "trade_no" if provider == "alipay" else "transaction_id"
        where.append(f"COALESCE(o.{column}, '') ILIKE %s")
        params.append(f"%{transaction}%")
    order_no = _text(filters.get("order_no") or filters.get("out_trade_no"))
    if order_no:
        column = "order_id" if provider == "wechat_shop" else "out_trade_no"
        where.append(f"COALESCE(o.{column}, '') ILIKE %s")
        params.append(f"%{order_no}%")
    created_from = _text(filters.get("created_from") or filters.get("date_from"))
    if created_from:
        where.append("o.created_at >= %s")
        params.append(created_from.replace("T", " "))
    created_to = _text(filters.get("created_to") or filters.get("date_to"))
    if created_to:
        where.append("o.created_at <= %s")
        params.append(created_to.replace("T", " "))
    paid_from = _text(filters.get("paid_from"))
    if paid_from:
        where.append("o.paid_at IS NOT NULL")
        where.append("o.paid_at >= %s")
        params.append(paid_from.replace("T", " "))
    paid_to = _text(filters.get("paid_to"))
    if paid_to:
        where.append("o.paid_at IS NOT NULL")
        where.append("o.paid_at <= %s")
        params.append(paid_to.replace("T", " "))
    is_paid = _text(filters.get("is_paid")).lower()
    if is_paid in {"1", "true", "yes", "y", "on"}:
        where.append("o.paid_at IS NOT NULL")
    elif is_paid in {"0", "false", "no", "n", "off"}:
        where.append("o.paid_at IS NULL")
    is_refunded = _text(filters.get("is_refunded")).lower()
    if is_refunded in {"1", "true", "yes", "y", "on"}:
        if provider == "wechat_shop":
            where.append("(COALESCE(o.refunded_amount_total, 0) > 0 OR COALESCE(o.on_aftersale_order_count, 0) > 0 OR o.returned_recorded IS TRUE)")
        elif provider == "wechat":
            where.append(
                f"""(
                    COALESCE(o.refunded_amount_total, 0) > 0
                    OR COALESCE(o.refund_status, '') IN ('requested', 'processing', 'refund_processing', 'partial_refunded', 'full_refunded')
                    OR EXISTS (
                        SELECT 1
                        FROM wechat_pay_refunds r
                        WHERE r.order_id = o.id
                          AND {active_wechat_refund_sql("r")}
                    )
                )"""
            )
        else:
            where.append("(COALESCE(o.refunded_amount_total, 0) > 0 OR COALESCE(o.refund_status, '') IN ('requested', 'processing', 'refund_processing', 'partial_refunded', 'full_refunded'))")
    elif is_refunded in {"0", "false", "no", "n", "off"}:
        if provider == "wechat_shop":
            where.append("(COALESCE(o.refunded_amount_total, 0) = 0 AND COALESCE(o.on_aftersale_order_count, 0) = 0 AND COALESCE(o.returned_recorded, FALSE) IS FALSE)")
        elif provider == "wechat":
            where.append(
                f"""(
                    COALESCE(o.refunded_amount_total, 0) = 0
                    AND COALESCE(o.refund_status, '') NOT IN ('requested', 'processing', 'refund_processing', 'partial_refunded', 'full_refunded')
                    AND NOT EXISTS (
                        SELECT 1
                        FROM wechat_pay_refunds r
                        WHERE r.order_id = o.id
                          AND {active_wechat_refund_sql("r")}
                    )
                )"""
            )
        else:
            where.append("(COALESCE(o.refunded_amount_total, 0) = 0 AND COALESCE(o.refund_status, '') NOT IN ('requested', 'processing', 'refund_processing', 'partial_refunded', 'full_refunded'))")
    status = _text(filters.get("status") or filters.get("payment_status"))
    if provider == "wechat_shop":
        if status == "paid":
            where.append("(o.deal_recorded IS TRUE AND o.returned_recorded IS FALSE)")
        elif status == "pending":
            where.append("o.business_status = 'pending'")
        elif status == "closed":
            where.append("(o.business_status = 'closed' OR o.status_code = 250)")
        elif status == "refund_processing":
            where.append("o.on_aftersale_order_count > 0")
        elif status == "partial_refunded":
            where.append("(o.returned_recorded IS TRUE AND o.amount_total > 0 AND o.refunded_amount_total > 0 AND o.refunded_amount_total < o.amount_total)")
        elif status == "full_refunded":
            where.append("(o.returned_recorded IS TRUE OR o.business_status = 'returned')")
    elif status == "paid":
        state_column = "trade_status" if provider == "alipay" else "trade_state"
        success_values = ("TRADE_SUCCESS", "TRADE_FINISHED") if provider == "alipay" else ("SUCCESS",)
        where.append(f"(o.status = 'paid' OR o.{state_column} IN ({', '.join(['%s'] * len(success_values))}))")
        params.extend(success_values)
    elif status == "pending":
        where.append("COALESCE(o.status, '') NOT IN ('paid', 'failed', 'closed')")
    elif status == "closed":
        state_column = "trade_status" if provider == "alipay" else "trade_state"
        closed_values = ("TRADE_CLOSED",) if provider == "alipay" else ("CLOSED", "REVOKED", "PAYERROR")
        where.append(f"(o.status IN ('closed', 'cancelled', 'canceled') OR o.{state_column} IN ({', '.join(['%s'] * len(closed_values))}))")
        params.extend(closed_values)
    elif status == "failed":
        where.append("o.status = 'failed'")
    elif status == "partial_refunded":
        where.append("(o.refund_status = 'partial_refunded' OR COALESCE(o.refunded_amount_total, 0) > 0)")
    elif status == "full_refunded":
        where.append("(o.refund_status = 'full_refunded' OR COALESCE(o.refunded_amount_total, 0) >= COALESCE(o.amount_total, 0))")
    return " AND ".join(where)


def _postgres_order_select(provider: str) -> str:
    if provider == "alipay":
        return """
            o.id, o.out_trade_no, o.trade_no, o.product_name, o.product_code, o.amount_total, o.currency,
            '' AS buyer_id, o.buyer_logon_id,
            COALESCE((SELECT identity.mobile FROM crm_user_identity identity WHERE identity.unionid = o.unionid LIMIT 1), '') AS mobile_snapshot,
            COALESCE((SELECT identity.primary_external_userid FROM crm_user_identity identity WHERE identity.unionid = o.unionid LIMIT 1), '') AS identity_snapshot,
            o.unionid, o.status, o.trade_status,
            o.notify_payload_json, o.return_payload_json, o.refunded_amount_total, o.refund_status,
            o.paid_at, o.created_at, o.updated_at, 0 AS active_refund_amount_total
        """
    if provider == "wechat_shop":
        return """
            o.id, o.order_id, o.order_id AS out_trade_no, o.transaction_id, o.product_name, o.product_code,
            o.amount_total, o.currency,
            {buyer_mobile_sql} AS buyer_mobile,
            {openid_sql} AS openid,
            o.unionid, o.business_status, o.status_code,
            o.deal_recorded, o.returned_recorded, o.raw_order_json AS notify_payload_json,
            o.refunded_amount_total, '' AS refund_status, o.on_aftersale_order_count,
            o.paid_at, o.created_at, o.updated_at,
            (
                SELECT COALESCE(SUM(r.refund_amount_total), 0)
                FROM wechat_shop_refunds r
                WHERE r.order_id = o.order_id
                  AND r.status NOT IN ('failed', 'closed', 'CLOSED', 'SUCCESS')
            ) AS active_refund_amount_total
        """.format(buyer_mobile_sql=WECHAT_SHOP_BUYER_MOBILE_SQL, openid_sql=WECHAT_SHOP_OPENID_SQL)
    return f"""
        o.id, o.out_trade_no, o.transaction_id, o.payer_name_snapshot,
        COALESCE(
            NULLIF((SELECT identity.mobile FROM crm_user_identity identity WHERE identity.unionid = o.unionid LIMIT 1), ''),
            NULLIF(o.metadata_json #>> '{{payer_identity,mobile}}', ''),
            NULLIF(o.metadata_json #>> '{{buyer_identity,mobile}}', ''),
            ''
        ) AS mobile_snapshot,
        '' AS userid_snapshot,
        COALESCE((SELECT identity.primary_external_userid FROM crm_user_identity identity WHERE identity.unionid = o.unionid LIMIT 1), '') AS external_userid,
        o.unionid, '' AS respondent_key, o.product_name, o.product_code, o.amount_total, o.currency,
        o.status, o.trade_state, o.notify_payload_json, o.refunded_amount_total, o.refund_status, o.paid_at,
        o.created_at, o.updated_at,
        (
            SELECT COALESCE(SUM(r.refund_amount_total), 0)
            FROM wechat_pay_refunds r
            WHERE r.order_id = o.id
              AND {active_wechat_refund_sql("r")}
        ) AS active_refund_amount_total
    """


def _postgres_table(provider: str) -> str:
    if provider == "alipay":
        return "alipay_pay_orders"
    if provider == "wechat_shop":
        return "wechat_shop_orders"
    return "wechat_pay_orders"


def _postgres_provider_clause(provider: str, clause: str) -> str:
    if provider == "wechat_shop":
        return f"({clause}) AND o.provider = 'wechat_shop'"
    return clause


def _postgres_unified_join(provider: str) -> str:
    if provider in {"wechat", "alipay"}:
        return "LEFT JOIN crm_user_identity identity ON identity.unionid = o.unionid"
    return ""


def _postgres_payload_keys_json(payload_expression: str) -> str:
    return f"""
        COALESCE(
            (
                SELECT jsonb_agg(payload_key ORDER BY payload_key)
                FROM (
                    SELECT payload_key
                    FROM jsonb_object_keys({payload_expression}) AS expanded(payload_key)
                    ORDER BY payload_key
                    LIMIT 12
                ) projected_payload_keys
            ),
            '[]'::jsonb
        )
    """


def _postgres_unified_order_json(provider: str) -> str:
    if provider == "alipay":
        callback_payload = "CASE WHEN o.notify_payload_json <> '{}'::jsonb THEN o.notify_payload_json ELSE o.return_payload_json END"
        return f"""
            jsonb_build_object(
                'id', o.id,
                'out_trade_no', o.out_trade_no,
                'trade_no', o.trade_no,
                'buyer_logon_id', o.buyer_logon_id,
                'mobile_snapshot', COALESCE(identity.mobile, ''),
                'identity_snapshot', COALESCE(identity.primary_external_userid, ''),
                'unionid', o.unionid,
                'product_name', o.product_name,
                'product_code', o.product_code,
                'amount_total', o.amount_total,
                'currency', o.currency,
                'status', o.status,
                'trade_status', o.trade_status,
                'refunded_amount_total', o.refunded_amount_total,
                'refund_status', o.refund_status,
                'active_refund_amount_total', 0,
                'notify_payload_present', o.notify_payload_json <> '{{}}'::jsonb,
                'return_payload_present', o.return_payload_json <> '{{}}'::jsonb,
                'callback_payload_keys', {_postgres_payload_keys_json(callback_payload)}
            )
        """
    if provider == "wechat_shop":
        return """
            jsonb_build_object(
                'id', o.id,
                'order_id', o.order_id,
                'out_trade_no', o.order_id,
                'transaction_id', o.transaction_id,
                'buyer_mobile', {buyer_mobile_sql},
                'openid', {openid_sql},
                'unionid', o.unionid,
                'product_name', o.product_name,
                'product_code', o.product_code,
                'product_count', o.product_count,
                'amount_total', o.amount_total,
                'currency', o.currency,
                'business_status', o.business_status,
                'status_code', o.status_code,
                'deal_recorded', o.deal_recorded,
                'returned_recorded', o.returned_recorded,
                'refunded_amount_total', o.refunded_amount_total,
                'refund_status', '',
                'on_aftersale_order_count', o.on_aftersale_order_count,
                'active_refund_amount_total', (
                    SELECT COALESCE(SUM(r.refund_amount_total), 0)
                    FROM wechat_shop_refunds r
                    WHERE r.order_id = o.order_id
                      AND r.status NOT IN ('failed', 'closed', 'CLOSED', 'SUCCESS')
                ),
                'notify_payload_present', o.raw_order_json <> '{{}}'::jsonb,
                'return_payload_present', false,
                'callback_payload_keys', {payload_keys_sql}
            )
        """.format(
            buyer_mobile_sql=WECHAT_SHOP_BUYER_MOBILE_SQL,
            openid_sql=WECHAT_SHOP_OPENID_SQL,
            payload_keys_sql=_postgres_payload_keys_json("o.raw_order_json"),
        )
    return f"""
        jsonb_build_object(
            'id', o.id,
            'out_trade_no', o.out_trade_no,
            'transaction_id', o.transaction_id,
            'payer_name_snapshot', o.payer_name_snapshot,
            'mobile_snapshot', COALESCE(
                NULLIF(identity.mobile, ''),
                NULLIF(o.metadata_json #>> '{{payer_identity,mobile}}', ''),
                NULLIF(o.metadata_json #>> '{{buyer_identity,mobile}}', ''),
                ''
            ),
            'userid_snapshot', '',
            'external_userid', COALESCE(identity.primary_external_userid, ''),
            'unionid', o.unionid,
            'product_name', o.product_name,
            'product_code', o.product_code,
            'amount_total', o.amount_total,
            'currency', o.currency,
            'status', o.status,
            'trade_state', o.trade_state,
            'refunded_amount_total', o.refunded_amount_total,
            'refund_status', o.refund_status,
            'active_refund_amount_total', (
                SELECT COALESCE(SUM(r.refund_amount_total), 0)
                FROM wechat_pay_refunds r
                WHERE r.order_id = o.id
                  AND {active_wechat_refund_sql("r")}
            ),
            'notify_payload_present', o.notify_payload_json <> '{{}}'::jsonb,
            'return_payload_present', false,
            'callback_payload_keys', {_postgres_payload_keys_json("o.notify_payload_json")}
        )
    """


def _postgres_unified_list(
    providers: list[str] | tuple[str, ...],
    filters: dict[str, Any],
    *,
    limit: int,
    offset: int,
    cursor: str | None,
    maximum: int,
) -> dict[str, Any]:
    selected = tuple(providers)
    if not selected or any(provider not in PROVIDERS for provider in selected):
        raise ValueError("provider must be one of wechat/alipay/wechat_shop")
    page_limit = max(1, min(_int(limit) or 50, max(1, int(maximum))))
    requested_offset = max(0, _int(offset))
    scope = unified_order_cursor_scope(selected, filters)
    decoded_cursor = decode_unified_order_cursor(cursor, expected_scope=scope)
    if cursor and requested_offset:
        raise ValueError("cursor and offset cannot be used together")
    keyset_cursor = decoded_cursor if isinstance(decoded_cursor, UnifiedOrderPageCursor) else None
    if isinstance(decoded_cursor, int):
        page_offset = decoded_cursor
    elif keyset_cursor is not None:
        page_offset = keyset_cursor.position
    else:
        page_offset = requested_offset
    sql_offset = 0 if keyset_cursor is not None else page_offset
    branch_limit = page_limit + 1 if keyset_cursor is not None else page_offset + page_limit + 1

    count_parts: list[str] = []
    count_params: list[Any] = []
    list_parts: list[str] = []
    list_params: list[Any] = []
    for provider in selected:
        provider_params: list[Any] = []
        clause = _postgres_provider_clause(
            provider,
            _postgres_filter_clause(provider, filters, provider_params),
        )
        table = _postgres_table(provider)
        count_parts.append(f"SELECT o.id FROM {table} o WHERE {clause}")
        count_params.extend(provider_params)

        cursor_clause = ""
        cursor_params: list[Any] = []
        if keyset_cursor is not None:
            cursor_clause = f"""
                AND (o.created_at, {PROVIDER_SORT_RANK[provider]}::integer, o.id)
                    < (%s::timestamptz, %s::integer, %s::bigint)
            """
            cursor_params.extend(
                [
                    keyset_cursor.created_at,
                    keyset_cursor.provider_rank,
                    keyset_cursor.source_id,
                ]
            )
        list_parts.append(
            f"""
            (
                SELECT
                    '{provider}'::text AS provider,
                    {PROVIDER_SORT_RANK[provider]}::integer AS provider_rank,
                    o.id::bigint AS source_id,
                    o.created_at AS sort_created_at,
                    o.paid_at,
                    {_postgres_unified_order_json(provider)} AS order_json
                FROM {table} o
                {_postgres_unified_join(provider)}
                WHERE {clause}
                {cursor_clause}
                ORDER BY o.created_at DESC, o.id DESC
                LIMIT %s
            )
            """
        )
        list_params.extend([*provider_params, *cursor_params, branch_limit])

    count_sql = f"""
        SELECT count(*) AS total
        FROM ({' UNION ALL '.join(count_parts)}) unified_count
    """
    list_sql = f"""
        SELECT provider, provider_rank, source_id, sort_created_at, paid_at, order_json
        FROM ({' UNION ALL '.join(list_parts)}) unified_orders
        ORDER BY sort_created_at DESC, provider_rank DESC, source_id DESC
        LIMIT %s OFFSET %s
    """
    with _connect() as conn:
        if "wechat" in selected:
            close_expired_wechat_pay_orders(conn=conn)
            conn.commit()
        total_row = conn.execute(count_sql, tuple(count_params)).fetchone() or {}
        rows = conn.execute(
            list_sql,
            tuple([*list_params, page_limit + 1, sql_offset]),
        ).fetchall()

    has_more = len(rows) > page_limit
    page_rows = list(rows[:page_limit])
    items: list[dict[str, Any]] = []
    for row in page_rows:
        mapped = dict(row)
        order = _json_dict(mapped.get("order_json"))
        order["created_at"] = mapped.get("sort_created_at")
        order["paid_at"] = mapped.get("paid_at")
        items.append(_present(_text(mapped.get("provider")), order))
    next_position = page_offset + len(page_rows)
    next_cursor = ""
    if has_more and page_rows:
        last = dict(page_rows[-1])
        next_cursor = encode_unified_order_cursor(
            created_at=last["sort_created_at"],
            provider_rank=int(last["provider_rank"]),
            source_id=int(last["source_id"]),
            position=next_position,
            scope=scope,
        )
    return {
        "items": items,
        "total": int(total_row.get("total") or 0),
        "limit": page_limit,
        "offset": page_offset,
        "has_more": has_more,
        "next_offset": next_position if has_more else None,
        "next_cursor": next_cursor,
    }


def _postgres_events(provider: str, out_trade_no: str) -> list[dict[str, Any]]:
    table = "alipay_pay_order_events" if provider == "alipay" else "wechat_shop_order_events" if provider == "wechat_shop" else "wechat_pay_order_events"
    column = "order_id" if provider == "wechat_shop" else "out_trade_no"
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE {column} = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 20
            """,
            (_text(out_trade_no),),
        ).fetchall()
    return [dict(row) for row in rows]


def _postgres_list(provider: str, filters: dict[str, Any], *, limit: int, offset: int) -> dict[str, Any]:
    limit = max(1, min(_int(limit) or 50, 100))
    offset = max(0, _int(offset))
    params: list[Any] = []
    clause = _postgres_filter_clause(provider, filters, params)
    table = _postgres_table(provider)
    select = _postgres_order_select(provider)
    with _connect() as conn:
        if provider == "wechat":
            close_expired_wechat_pay_orders(conn=conn)
            conn.commit()
        total = int((conn.execute(f"SELECT count(*) AS total FROM {table} o WHERE {clause}", tuple(params)).fetchone() or {}).get("total") or 0)
        rows = conn.execute(
            f"""
            SELECT {select}
            FROM {table} o
            WHERE {clause}
            ORDER BY o.created_at DESC, o.id DESC
            LIMIT %s OFFSET %s
            """,
            tuple([*params, limit, offset]),
        ).fetchall()
    return {
        "items": [_present(provider, dict(row)) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def _postgres_get(provider: str, identifier: str) -> dict[str, Any] | None:
    table = _postgres_table(provider)
    select = _postgres_order_select(provider)
    transaction_column = "trade_no" if provider == "alipay" else "transaction_id"
    order_column = "order_id" if provider == "wechat_shop" else "out_trade_no"
    with _connect() as conn:
        if provider == "wechat":
            close_expired_wechat_pay_orders(conn=conn, out_trade_no=_text(identifier), limit=1)
            conn.commit()
        row = conn.execute(
            f"""
            SELECT {select}
            FROM {table} o
            WHERE o.id::text = %s OR o.{order_column} = %s OR o.{transaction_column} = %s
            LIMIT 1
            """,
            (_text(identifier), _text(identifier), _text(identifier)),
        ).fetchone()
    if not row:
        return None
    row_dict = dict(row)
    return _present(provider, row_dict, events=_postgres_events(provider, _text(row_dict.get("out_trade_no"))))


def _fixture_list(provider: str, filters: dict[str, Any], *, limit: int, offset: int) -> dict[str, Any]:
    if provider == "wechat_shop":
        rows = fixture_wechat_shop_orders()
        order_no = _text(filters.get("order_no") or filters.get("out_trade_no"))
        if order_no:
            rows = [item for item in rows if order_no in _text(item.get("order_id"))]
        status_filter = _text(filters.get("status") or filters.get("payment_status"))
        items = [_present(provider, row) for row in rows]
        if status_filter in PaymentProviderStatusMapper.LABELS:
            items = [item for item in items if item["status"] == status_filter]
        return {"items": items[offset : offset + limit], "total": len(items), "limit": limit, "offset": offset, "has_more": offset + limit < len(items), "next_offset": offset + limit if offset + limit < len(items) else None}
    payload = ListTransactionsQuery(provider)(filters, limit=limit, offset=offset)
    items = [_present(provider, row) for row in payload.get("items", [])]
    order_no = _text(filters.get("order_no") or filters.get("out_trade_no"))
    if order_no:
        items = [item for item in items if order_no in _text(item.get("out_trade_no") or item.get("order_no") or item.get("merchant_order_no"))]
    status_filter = _text(filters.get("status") or filters.get("payment_status"))
    if status_filter in PaymentProviderStatusMapper.LABELS:
        items = [item for item in items if item["status"] == status_filter]
    total = len(items) if status_filter in PaymentProviderStatusMapper.LABELS else _int(payload.get("total") or len(items))
    return {"items": items, "total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total, "next_offset": offset + limit if offset + limit < total else None}


def _fixture_get(provider: str, identifier: str) -> dict[str, Any] | None:
    if provider == "wechat_shop":
        order = fixture_wechat_shop_order(identifier)
        return _present(provider, order) if order else None
    try:
        payload = GetTransactionQuery(provider)(_text(identifier))
    except Exception:
        return None
    return _present(provider, dict(payload.get("transaction") or {}))


class CommerceAdminTransactionListReadModel:
    def __init__(self, provider: str) -> None:
        self.provider = provider_config(provider).provider

    def execute(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        payload = (
            _postgres_list(self.provider, dict(filters or {}), limit=limit, offset=offset)
            if database_mode() == "postgres"
            else _fixture_list(self.provider, dict(filters or {}), limit=limit, offset=offset)
        )
        return {
            "ok": True,
            "provider": self.provider,
            "provider_label": provider_config(self.provider).provider_label,
            "filters": dict(filters or {}),
            **payload,
        }


class CommerceAdminUnifiedTransactionListReadModel:
    def execute(
        self,
        providers: list[str] | tuple[str, ...],
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
        cursor: str | None = None,
        maximum: int = 100,
    ) -> dict[str, Any]:
        if database_mode() != "postgres":
            raise RuntimeError("unified order SQL read model requires postgres")
        return {
            "ok": True,
            "providers": list(providers),
            "filters": dict(filters or {}),
            **_postgres_unified_list(
                providers,
                dict(filters or {}),
                limit=limit,
                offset=offset,
                cursor=cursor,
                maximum=maximum,
            ),
        }


class CommerceAdminTransactionDetailReadModel:
    def __init__(self, provider: str) -> None:
        self.provider = provider_config(provider).provider

    def execute(self, identifier: str) -> dict[str, Any]:
        order = _postgres_get(self.provider, identifier) if database_mode() == "postgres" else _fixture_get(self.provider, identifier)
        if not order:
            raise NotFoundError("transaction not found")
        return {
            "ok": True,
            "provider": self.provider,
            "provider_label": provider_config(self.provider).provider_label,
            "transaction": order,
        }
