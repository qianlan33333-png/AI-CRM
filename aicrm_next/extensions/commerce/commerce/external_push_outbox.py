from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aicrm_next.platform.external_push.domain_event_outbox_port import build_domain_event_outbox_port

from .product_code_aliases import canonical_product_code, product_code_filter_values

EVENT_TRANSACTION_PAID = "transaction.paid"
DEFAULT_TENANT_ID = "aicrm"
AGGREGATE_TYPE_WECHAT_PAY_ORDER = "wechat_pay_order"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any = None) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        text = _text(value)
        if text:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return text
        else:
            dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_product_for_order(conn: Any, order: dict[str, Any]) -> dict[str, Any]:
    product_code = _text(order.get("product_code"))
    candidates = product_code_filter_values(product_code) or [canonical_product_code(product_code)]
    row = conn.execute(
        """
        SELECT id, product_code, name, amount_total
        FROM wechat_pay_products
        WHERE product_code = ANY(%s)
        ORDER BY
            CASE
                WHEN product_code = %s THEN 0
                WHEN product_code = %s THEN 1
                ELSE 2
            END,
            updated_at DESC NULLS LAST,
            id DESC
        LIMIT 1
        """,
        (candidates, product_code, canonical_product_code(product_code)),
    ).fetchone()
    if row:
        return dict(row)
    return {
        "id": 0,
        "product_code": product_code,
        "name": _text(order.get("product_name") or product_code),
        "amount_total": int(order.get("amount_total") or 0),
    }


def build_transaction_paid_outbox_payload(order: dict[str, Any], product: dict[str, Any] | None = None) -> dict[str, Any]:
    product = dict(product or {})
    return {
        "order_id": str(order.get("id") or ""),
        "product_id": str(product.get("id") or ""),
        "product_code": _text(order.get("product_code")),
        "tenant_id": DEFAULT_TENANT_ID,
        "buyer_id": _text(order.get("external_userid") or order.get("userid_snapshot") or order.get("respondent_key")),
        "paid_amount": int(order.get("payer_total") or order.get("amount_total") or 0),
        "paid_at": _iso(order.get("paid_at")),
        "pay_channel": "wechat",
    }


def is_order_paid(order: dict[str, Any]) -> bool:
    return _text(order.get("status")) == "paid" or _text(order.get("trade_state")) == "SUCCESS"


def enqueue_transaction_paid_outbox(conn: Any, order: dict[str, Any]) -> dict[str, Any] | None:
    if not is_order_paid(order):
        return None
    product = resolve_product_for_order(conn, order)
    payload = build_transaction_paid_outbox_payload(order, product)
    aggregate_id = _text(order.get("id") or order.get("out_trade_no"))
    if not aggregate_id:
        raise ValueError("wechat_pay_order aggregate_id is required")
    return build_domain_event_outbox_port().enqueue_dbapi(
        conn,
        tenant_id=DEFAULT_TENANT_ID,
        event_type=EVENT_TRANSACTION_PAID,
        aggregate_type=AGGREGATE_TYPE_WECHAT_PAY_ORDER,
        aggregate_id=aggregate_id,
        payload=payload,
    )
