from __future__ import annotations

from datetime import datetime
from typing import Any

from aicrm_next.shared.runtime import database_mode

from .admin_transactions import create_wechat_refund_request
from .admin_unified_orders import ROUTE_OWNER, normalize_limit, normalize_offset, normalize_provider
from .repo import connect_commerce_db
from .wechat_shop_service import create_wechat_shop_refund_request, fixture_wechat_shop_refunds


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _money_yuan(value: Any) -> str:
    return f"{_int(value) / 100:.2f}"


def _format_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return _text(value)


def _connect():
    return connect_commerce_db()


def _status_label(status: str) -> str:
    return {
        "requested": "退款申请已提交",
        "PROCESSING": "退款处理中",
        "SUCCESS": "退款成功",
        "CLOSED": "退款关闭",
        "ABNORMAL": "退款异常",
        "failed": "退款申请失败",
    }.get(_text(status), _text(status) or "退款申请已提交")


def _filters(source: dict[str, Any] | None) -> dict[str, str]:
    payload = dict(source or {})
    return {
        "order_no": _text(payload.get("order_no") or payload.get("out_trade_no")),
        "out_trade_no": _text(payload.get("out_trade_no") or payload.get("order_no")),
        "transaction_id": _text(payload.get("transaction_id")),
        "refund_id": _text(payload.get("refund_id")),
        "out_refund_no": _text(payload.get("out_refund_no")),
        "status": _text(payload.get("status")),
        "created_from": _text(payload.get("created_from")),
        "created_to": _text(payload.get("created_to")),
    }


def _present(provider: str, row: dict[str, Any]) -> dict[str, Any]:
    refund_amount = _int(row.get("refund_amount_total") or row.get("amount_total"))
    order_amount = _int(row.get("order_amount_total"))
    status = _text(row.get("status"))
    return {
        "provider": provider,
        "provider_label": {"wechat": "微信支付", "alipay": "支付宝", "wechat_shop": "微信小店"}.get(provider, provider),
        "order_no": _text(row.get("out_trade_no") or row.get("order_no")),
        "out_trade_no": _text(row.get("out_trade_no") or row.get("order_no")),
        "transaction_id": _text(row.get("transaction_id") or row.get("trade_no")),
        "out_refund_no": _text(row.get("out_refund_no")),
        "refund_id": _text(row.get("refund_id")),
        "refund_amount_total": refund_amount,
        "refund_amount_yuan": _money_yuan(refund_amount),
        "order_amount_total": order_amount,
        "currency": _text(row.get("currency")) or "CNY",
        "status": status,
        "status_label": _status_label(status),
        "reason": _text(row.get("reason")),
        "requested_by": _text(row.get("requested_by") or row.get("operator")),
        "operator": _text(row.get("operator") or row.get("requested_by")),
        "created_at": _format_time(row.get("created_at")),
        "updated_at": _format_time(row.get("updated_at")),
    }


def _postgres_provider_refunds(provider: str, filters: dict[str, str], *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int, list[str]]:
    if provider == "wechat_shop":
        table = "wechat_shop_refunds"
        select = """
            order_id AS out_trade_no, transaction_id, aftersale_id AS refund_id, out_refund_no,
            refund_amount_total, order_amount_total, currency, status, reason, requested_by,
            operator, created_at, updated_at
        """
    else:
        table = "wechat_pay_refunds" if provider == "wechat" else "alipay_pay_refunds"
        select = "*"
    where = ["1 = 1"]
    params: list[Any] = []
    if filters["order_no"]:
        where.append("COALESCE(out_trade_no, '') ILIKE %s")
        params.append(f"%{filters['order_no']}%")
    for key in ("transaction_id", "refund_id", "out_refund_no", "status"):
        if filters[key]:
            where.append(f"COALESCE({key}, '') ILIKE %s")
            params.append(f"%{filters[key]}%")
    if filters["created_from"]:
        where.append("created_at >= %s")
        params.append(filters["created_from"].replace("T", " "))
    if filters["created_to"]:
        where.append("created_at <= %s")
        params.append(filters["created_to"].replace("T", " "))
    clause = " AND ".join(where)
    try:
        with _connect() as conn:
            total = int((conn.execute(f"SELECT count(*) AS total FROM {table} WHERE {clause}", tuple(params)).fetchone() or {}).get("total") or 0)
            rows = conn.execute(
                f"""
                SELECT {select}
                FROM {table}
                WHERE {clause}
                ORDER BY created_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                tuple([*params, limit, offset]),
            ).fetchall()
        return [_present(provider, dict(row)) for row in rows], total, []
    except Exception as exc:
        return [], 0, [f"{provider} refund table unavailable: {exc}"]


def list_refunds(
    *,
    provider: str = "all",
    filters: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    normalized_provider = normalize_provider(provider)
    if normalized_provider == "auto":
        normalized_provider = "all"
    page_limit = normalize_limit(limit)
    page_offset = normalize_offset(offset)
    normalized_filters = _filters(filters)
    warnings: list[str] = []
    selected = ["wechat", "alipay", "wechat_shop"] if normalized_provider == "all" else [normalized_provider]
    refunds: list[dict[str, Any]] = []
    total = 0
    if database_mode() == "postgres":
        for provider_name in selected:
            rows, count, provider_warnings = _postgres_provider_refunds(provider_name, normalized_filters, limit=page_limit, offset=page_offset)
            refunds.extend(rows)
            total += count
            warnings.extend(provider_warnings)
    elif normalized_provider == "wechat_shop":
        rows = [_present("wechat_shop", row) for row in fixture_wechat_shop_refunds()]
        refunds.extend(rows)
        total = len(rows)
    elif normalized_provider == "alipay":
        warnings.append("alipay refund fixture table is not available in this slice")
    return {
        "ok": True,
        "refunds": refunds[:page_limit],
        "total": total if database_mode() == "postgres" else len(refunds),
        "limit": page_limit,
        "offset": page_offset,
        "warnings": warnings,
        "route_owner": ROUTE_OWNER,
        "source_status": "next_admin_refunds",
        "fallback_used": False,
    }


def request_refund(payload: dict[str, Any]) -> dict[str, Any]:
    provider = normalize_provider(payload.get("provider") or "wechat", default="wechat")
    if provider == "alipay":
        raise ValueError("provider_refund_not_supported")
    if provider == "wechat_shop":
        order_no = _text(payload.get("order_no") or payload.get("out_trade_no"))
        if not order_no:
            raise ValueError("order_no is required")
        result = create_wechat_shop_refund_request(order_no, payload)
        return {
            **result,
            "route_owner": ROUTE_OWNER,
            "source_status": "next_admin_refund_request",
            "fallback_used": False,
        }
    if provider != "wechat":
        raise ValueError("provider_refund_not_supported")
    order_no = _text(payload.get("order_no") or payload.get("out_trade_no"))
    if not order_no:
        raise ValueError("order_no is required")
    result = create_wechat_refund_request(order_no, payload)
    return {
        **result,
        "route_owner": ROUTE_OWNER,
        "source_status": "next_admin_refund_request",
        "fallback_used": False,
    }
