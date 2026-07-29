from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import datetime, timedelta
from importlib.util import find_spec
from typing import Any
from zoneinfo import ZoneInfo

from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, PAYMENT_WECHAT_REFUND_REQUEST
from aicrm_next.platform.platform_foundation.internal_events.outbox import enqueue_transactional_internal_event_outbox
from aicrm_next.platform.platform_foundation.internal_events.refund import build_refund_succeeded_event_request
from aicrm_next.platform.shared.runtime import database_mode, raw_database_url
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting
from aicrm_next.platform.shared.text_encoding import repair_utf8_mojibake

from .repo import build_commerce_repository, connect_commerce_db
from .application import GetTransactionQuery, ListProductsQuery, ListTransactionsQuery
from .order_expiration import close_expired_wechat_pay_orders
from .product_code_aliases import canonical_product_code, product_code_filter_values
from .refund_status import active_wechat_refund_sql
from .wechat_pay_order_write_port import build_wechat_pay_order_write_port
from aicrm_next.integration_ports import WeChatPayClient, WeChatPayClientConfig, wechat_pay_client_config_from_env

ADMIN_TZ = ZoneInfo("Asia/Shanghai")
ALLOWED_LIMITS = {20, 50, 100}
STATUS_LABELS = {
    "pending": "待支付",
    "paid": "已支付",
    "refund_processing": "退款处理中",
    "partial_refunded": "部分退款",
    "full_refunded": "全额退款",
    "failed": "支付失败",
}


def default_filters() -> dict[str, str]:
    now = datetime.now(ADMIN_TZ)
    start = now - timedelta(days=30)
    return {
        "created_from": start.strftime("%Y-%m-%dT00:00"),
        "created_to": now.strftime("%Y-%m-%dT23:59"),
        "product_code": "",
        "status": "",
        "mobile": "",
        "identity": "",
        "transaction_id": "",
    }


def normalize_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 20
    return limit if limit in ALLOWED_LIMITS else 20


def normalize_offset(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def normalize_filters(source: dict[str, Any] | None) -> dict[str, str]:
    payload = dict(source or {})
    status = str(payload.get("status") or payload.get("payment_status") or "").strip()
    if status and status not in STATUS_LABELS:
        status = ""
    return {
        "created_from": str(payload.get("created_from") or payload.get("date_from") or "").strip(),
        "created_to": str(payload.get("created_to") or payload.get("date_to") or "").strip(),
        "product_code": str(payload.get("product_code") or "").strip(),
        "status": status,
        "mobile": str(payload.get("mobile") or payload.get("mobile_snapshot") or "").strip(),
        "identity": str(payload.get("identity") or payload.get("external_userid") or "").strip(),
        "transaction_id": str(payload.get("transaction_id") or "").strip(),
    }


def _database_url() -> str:
    return raw_database_url()


def _psycopg_available() -> bool:
    return find_spec("psycopg") is not None


def _require_psycopg(message: str) -> None:
    if not _psycopg_available():
        raise RuntimeError(message)


def _format_time(value: Any) -> str:
    if isinstance(value, datetime):
        source = value
        if source.tzinfo is None:
            source = source.replace(tzinfo=ADMIN_TZ)
        return source.astimezone(ADMIN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "")


def _money_yuan(value: Any) -> str:
    try:
        cents = int(value or 0)
    except (TypeError, ValueError):
        cents = 0
    return f"{cents / 100:.2f}"


def _normalized_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _refund_status_label(status: str) -> str:
    mapping = {
        "queued": "退款申请已入队",
        "requested": "退款申请已提交",
        "PROCESSING": "退款处理中",
        "SUCCESS": "退款成功",
        "CLOSED": "退款关闭",
        "ABNORMAL": "退款异常",
        "failed": "退款申请失败",
    }
    return mapping.get(str(status or "").strip(), str(status or "").strip() or "退款申请已提交")


def _out_refund_no() -> str:
    return "WXR" + datetime.now(ADMIN_TZ).strftime("%y%m%d%H%M%S") + secrets.token_hex(4).upper()


def _wechat_pay_client_config() -> WeChatPayClientConfig:
    return wechat_pay_client_config_from_env()


def _create_wechat_pay_refund_client() -> WeChatPayClient:
    return WeChatPayClient(_wechat_pay_client_config())


def mark_wechat_refund_request_failed(
    out_refund_no: str,
    *,
    error_code: str = "",
    error_message: str = "",
    response_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if database_mode() != "postgres":
        return {"ok": False, "reason": "postgres_required"}
    refund_no = str(out_refund_no or "").strip()
    if not refund_no:
        return {"ok": False, "reason": "out_refund_no_required"}
    try:
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError as exc:
        raise RuntimeError("psycopg is required for production refund failure sync") from exc

    payload = {
        "error_code": str(error_code or "").strip(),
        "error_message": str(error_message or "").strip()[:500],
        **dict(response_payload or {}),
    }
    with connect_commerce_db(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wechat_pay_refunds
                SET status = 'failed',
                    response_payload_json = %s,
                    error_message = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE out_refund_no = %s
                RETURNING out_trade_no, transaction_id
                """,
                (Jsonb(payload), str(error_message or "").strip()[:500], refund_no),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return {"ok": False, "reason": "refund_record_not_found"}
            cur.execute(
                """
                INSERT INTO wechat_pay_order_events (
                    out_trade_no, event_type, transaction_id, trade_state,
                    payload_json, headers_json, created_at
                )
                VALUES (%s, 'refund_failed', %s, '', %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    str(row.get("out_trade_no") or ""),
                    str(row.get("transaction_id") or ""),
                    Jsonb({"out_refund_no": refund_no, **payload, "provider_refund_executed": bool(payload.get("real_external_call_executed"))}),
                    Jsonb({}),
                ),
            )
        conn.commit()
    return {"ok": True, "out_refund_no": refund_no}


def _normalize_refund_provider_status(payload: dict[str, Any]) -> str:
    status = str(payload.get("refund_status") or payload.get("status") or "").strip()
    return status.upper() if status else "PROCESSING"


def _refund_event_type(status: str) -> str:
    return {
        "SUCCESS": "refund_succeeded",
        "CLOSED": "refund_closed",
        "ABNORMAL": "refund_abnormal",
    }.get(status, "refund_synced")


def _refund_payload_amount(payload: dict[str, Any], fallback: Any) -> int:
    amount = payload.get("amount") if isinstance(payload.get("amount"), dict) else {}
    return _int_value(amount.get("refund") or payload.get("refund_amount_total") or fallback)


def _merged_status(row: dict[str, Any]) -> str:
    try:
        amount_total = int(row.get("amount_total") or row.get("amount_cents") or 0)
        refunded = int(row.get("refunded_amount_total") or 0)
        active_refunding = int(row.get("active_refund_amount_total") or 0)
    except (TypeError, ValueError):
        amount_total = 0
        refunded = 0
        active_refunding = 0
    refund_status = str(row.get("refund_status") or "").strip()
    raw = str(row.get("status") or row.get("payment_status") or "").strip()
    trade_state = str(row.get("trade_state") or "").strip()
    if refund_status == "full_refunded" or (amount_total > 0 and refunded >= amount_total):
        return "full_refunded"
    if active_refunding > 0:
        return "refund_processing"
    if refund_status == "partial_refunded" or refunded > 0:
        return "partial_refunded"
    if raw == "paid" or trade_state == "SUCCESS":
        return "paid"
    if raw == "failed":
        return "failed"
    return "pending"


def _present_order(row: dict[str, Any]) -> dict[str, Any]:
    status = _merged_status(row)
    order_id = row.get("id") or row.get("order_no") or ""
    amount_total = _int_value(row.get("amount_total") or row.get("amount_cents"))
    refunded = max(0, _int_value(row.get("refunded_amount_total")))
    active_refunding = max(0, _int_value(row.get("active_refund_amount_total")))
    refundable = max(0, amount_total - refunded - active_refunding)
    payer_name = repair_utf8_mojibake(row.get("payer_name_snapshot") or row.get("payer_name")) or "未记录付款人"
    mobile = str(row.get("mobile_snapshot") or row.get("buyer_mobile") or "").strip()
    userid = str(row.get("userid_snapshot") or "").strip()
    external_userid = str(row.get("external_userid") or "").strip()
    unionid = str(row.get("unionid") or "").strip()
    product_code = canonical_product_code(row.get("product_code"))
    product_name = str(row.get("product_name") or row.get("product_title") or product_code).strip()
    transaction_id = str(row.get("transaction_id") or "").strip()
    return {
        "id": order_id,
        "out_trade_no": str(row.get("out_trade_no") or row.get("order_no") or ""),
        "created_at": _format_time(row.get("created_at")),
        "transaction_id": transaction_id or "待支付暂无微信单号",
        "has_transaction_id": bool(transaction_id),
        "payer_name": payer_name or "未记录付款人",
        "mobile": mobile,
        "unionid": unionid,
        "userid": userid,
        "external_userid": external_userid,
        "product_code": product_code,
        "product_name": product_name or "-",
        "amount_total": amount_total,
        "amount_yuan": _money_yuan(amount_total),
        "currency": str(row.get("currency") or "CNY"),
        "status": status,
        "status_label": STATUS_LABELS[status],
        "refunded_amount_total": refunded,
        "refunded_amount_yuan": _money_yuan(refunded),
        "active_refund_amount_total": active_refunding,
        "active_refund_amount_yuan": _money_yuan(active_refunding),
        "refundable_amount_total": refundable,
        "refundable_amount_yuan": _money_yuan(refundable),
        "can_refund": status in {"paid", "partial_refunded"} and refundable > 0,
        "detail_url": f"/admin/wechat-pay/transactions/{order_id}",
    }


def _fixture_orders(filters: dict[str, str], *, limit: int, offset: int) -> dict[str, Any]:
    status_filter = filters.get("status")
    payload = ListTransactionsQuery("wechat")(
        {
            "payment_status": status_filter if status_filter in {"pending", "paid", "failed"} else "",
            "product_code": "",
            "mobile": filters.get("mobile"),
            "external_userid": filters.get("identity"),
            "date_from": filters.get("created_from"),
            "date_to": filters.get("created_to"),
        },
        limit=limit,
        offset=offset,
    )
    rows = payload.get("items", [])
    product_codes = set(product_code_filter_values(filters.get("product_code")))
    if product_codes:
        rows = [row for row in rows if str(row.get("product_code") or "").strip() in product_codes]
    if filters.get("transaction_id"):
        rows = [row for row in rows if filters["transaction_id"] in str(row.get("transaction_id") or "")]
    items = [_present_order(row) for row in rows]
    if status_filter and status_filter not in {"pending", "paid", "failed"}:
        items = [item for item in items if item["status"] == status_filter]
    total = len(items) if status_filter and status_filter not in {"pending", "paid", "failed"} else int(payload.get("total") or len(rows))
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _postgres_order_select() -> str:
    return f"""
        id, out_trade_no, transaction_id, payer_name_snapshot,
        COALESCE(
            NULLIF((SELECT identity.mobile FROM crm_user_identity identity WHERE identity.unionid = wechat_pay_orders.unionid LIMIT 1), ''),
            NULLIF(metadata_json #>> '{{payer_identity,mobile}}', ''),
            NULLIF(metadata_json #>> '{{buyer_identity,mobile}}', ''),
            ''
        ) AS mobile_snapshot,
        '' AS userid_snapshot,
        COALESCE((SELECT identity.primary_external_userid FROM crm_user_identity identity WHERE identity.unionid = wechat_pay_orders.unionid LIMIT 1), '') AS external_userid,
        unionid, '' AS respondent_key, product_name, product_code, amount_total, currency,
        status, trade_state, refund_status, refunded_amount_total, created_at,
        (
            SELECT COALESCE(SUM(r.refund_amount_total), 0)
            FROM wechat_pay_refunds r
            WHERE r.order_id = wechat_pay_orders.id
              AND {active_wechat_refund_sql("r")}
        ) AS active_refund_amount_total
    """


def _postgres_orders(filters: dict[str, str], *, limit: int, offset: int) -> dict[str, Any]:
    _require_psycopg("psycopg is required for production transaction admin")

    where = ["1 = 1"]
    params: list[Any] = []
    if filters.get("product_code"):
        filter_values = product_code_filter_values(filters["product_code"])
        where.append(f"product_code IN ({', '.join(['%s'] * len(filter_values))})")
        params.extend(filter_values)
    if filters.get("mobile"):
        where.append(
            """
            EXISTS (
                SELECT 1 FROM crm_user_identity identity
                WHERE identity.unionid = wechat_pay_orders.unionid
                  AND COALESCE(identity.mobile, '') ILIKE %s
            )
            """
        )
        params.append(f"%{filters['mobile']}%")
    if filters.get("identity"):
        where.append(
            """
            (
                COALESCE(wechat_pay_orders.unionid, '') ILIKE %s
                OR EXISTS (
                    SELECT 1 FROM crm_user_identity identity
                    WHERE identity.unionid = wechat_pay_orders.unionid
                      AND (
                          COALESCE(identity.primary_external_userid, '') ILIKE %s
                          OR COALESCE(identity.primary_openid, '') ILIKE %s
                      )
                )
            )
            """
        )
        needle = f"%{filters['identity']}%"
        params.extend([needle, needle, needle])
    if filters.get("transaction_id"):
        where.append("COALESCE(transaction_id, '') ILIKE %s")
        params.append(f"%{filters['transaction_id']}%")
    if filters.get("created_from"):
        where.append("created_at >= %s")
        params.append(filters["created_from"].replace("T", " "))
    if filters.get("created_to"):
        where.append("created_at <= %s")
        params.append(filters["created_to"].replace("T", " "))
    if filters.get("status") == "paid":
        where.append("(status = 'paid' OR trade_state = 'SUCCESS')")
    elif filters.get("status") == "pending":
        where.append("COALESCE(status, '') NOT IN ('paid', 'failed', 'closed') AND COALESCE(trade_state, '') <> 'SUCCESS'")
    elif filters.get("status") == "refund_processing":
        where.append(
            f"""
            EXISTS (
                SELECT 1 FROM wechat_pay_refunds r
                WHERE r.order_id = wechat_pay_orders.id
                  AND {active_wechat_refund_sql("r")}
            )
            """
        )
    elif filters.get("status") == "failed":
        where.append("status = 'failed'")
    elif filters.get("status") == "partial_refunded":
        where.append("(refund_status = 'partial_refunded' OR COALESCE(refunded_amount_total, 0) > 0)")
    elif filters.get("status") == "full_refunded":
        where.append("(refund_status = 'full_refunded' OR COALESCE(refunded_amount_total, 0) >= COALESCE(amount_total, 0))")

    clause = " AND ".join(where)
    query = f"""
        SELECT {_postgres_order_select()}
        FROM wechat_pay_orders
        WHERE {clause}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
    """
    count_query = f"SELECT count(*) AS total FROM wechat_pay_orders WHERE {clause}"
    with connect_commerce_db(_database_url()) as conn:
        close_expired_wechat_pay_orders(conn=conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(count_query, tuple(params))
            total = int((cur.fetchone() or {}).get("total") or 0)
            cur.execute(query, tuple([*params, limit, offset]))
            rows = [dict(row) for row in cur.fetchall()]
    return {
        "items": [_present_order(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def list_wechat_admin_orders(filters: dict[str, Any] | None, *, limit: Any = 20, offset: Any = 0) -> dict[str, Any]:
    normalized = normalize_filters(filters)
    page_size = normalize_limit(limit)
    page_offset = normalize_offset(offset)
    payload = (
        _postgres_orders(normalized, limit=page_size, offset=page_offset)
        if database_mode() == "postgres"
        else _fixture_orders(normalized, limit=page_size, offset=page_offset)
    )
    return {"ok": True, "filters": normalized, **payload}


def get_wechat_admin_order(order_id: str) -> dict[str, Any] | None:
    identifier = str(order_id or "").strip()
    if not identifier:
        return None
    if database_mode() == "postgres":
        _require_psycopg("psycopg is required for production transaction admin")
        with connect_commerce_db(_database_url()) as conn:
            close_expired_wechat_pay_orders(conn=conn, out_trade_no=identifier, limit=1)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_postgres_order_select()}
                    FROM wechat_pay_orders
                    WHERE id::text = %s OR out_trade_no = %s OR transaction_id = %s
                    LIMIT 1
                    """,
                    (identifier, identifier, identifier),
                )
                row = cur.fetchone()
                return _present_order(dict(row)) if row else None
    try:
        payload = GetTransactionQuery("wechat")(identifier)
    except Exception:
        return None
    return _present_order(payload.get("transaction", {}))


def _validate_refund_request(order: dict[str, Any], payload: dict[str, Any]) -> int:
    if not order:
        raise ValueError("订单不存在")
    if not order.get("can_refund"):
        raise ValueError("只有已支付或部分退款且仍有可退金额的订单可以申请退款")
    transaction_id = str(order.get("transaction_id") or "").strip()
    if not transaction_id or str(payload.get("transaction_id_confirmation") or "").strip() != transaction_id:
        raise ValueError("微信单号二次确认不匹配")
    if not _normalized_bool(payload.get("checked")):
        raise ValueError("请先勾选已核对付款人、商品、金额和微信单号")
    amount_total = _int_value(payload.get("refund_amount_total"))
    if amount_total <= 0:
        raise ValueError("退款金额必须大于 0")
    if amount_total > _int_value(order.get("refundable_amount_total")):
        raise ValueError("累计退款金额不能超过订单金额")
    if not str(payload.get("reason") or "").strip():
        raise ValueError("请选择退款原因")
    return amount_total


def _refund_request_payload(
    order: dict[str, Any],
    *,
    out_refund_no: str,
    amount_total: int,
    reason: str,
    notify_url: str,
) -> dict[str, Any]:
    currency = str(order.get("currency") or "CNY").strip() or "CNY"
    request_payload: dict[str, Any] = {
        "transaction_id": str(order.get("transaction_id") or "").strip(),
        "out_refund_no": out_refund_no,
        "reason": reason[:80],
        "amount": {
            "refund": int(amount_total),
            "total": int(order.get("amount_total") or 0),
            "currency": currency,
        },
    }
    if notify_url:
        request_payload["notify_url"] = notify_url
    return request_payload


def _locked_refundable_wechat_order(conn: Any, order_db_id: int) -> dict[str, Any]:
    # Acquire the order lock in its own statement. Under READ COMMITTED, a
    # concurrent waiter then gets a fresh snapshot for the aggregate query
    # below and sees refund rows committed by the previous lock owner. Keeping
    # the aggregate inside the locking statement would retain the pre-wait
    # statement snapshot and can permit concurrent over-refund.
    locked = conn.execute(
        "SELECT id FROM wechat_pay_orders WHERE id = %s FOR UPDATE",
        (int(order_db_id),),
    ).fetchone()
    if not locked:
        return {}
    row = conn.execute(
        f"""
        SELECT o.*,
               COALESCE((
                   SELECT SUM(r.refund_amount_total)
                   FROM wechat_pay_refunds r
                   WHERE r.order_id = o.id
                     AND {active_wechat_refund_sql("r")}
               ), 0) AS active_refund_amount_total
        FROM wechat_pay_orders o
        WHERE o.id = %s
        """,
        (int(order_db_id),),
    ).fetchone()
    return _present_order(dict(row)) if row else {}


def create_wechat_refund_request(order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    order = get_wechat_admin_order(order_id) or {}
    amount_total = _validate_refund_request(order, payload)
    reason = str(payload.get("reason") or "").strip()
    out_refund_no = _out_refund_no()
    refund_notify_url = str(
        payload.get("refund_notify_url")
        or payload.get("notify_url")
        or managed_runtime_setting("WECHAT_PAY_REFUND_NOTIFY_URL")
        or ""
    ).strip()
    request_payload = _refund_request_payload(
        order,
        out_refund_no=out_refund_no,
        amount_total=amount_total,
        reason=reason,
        notify_url=refund_notify_url,
    )
    if database_mode() == "postgres":
        try:
            from psycopg.types.json import Jsonb
        except ModuleNotFoundError as exc:
            raise RuntimeError("psycopg is required for production transaction admin") from exc
        with connect_commerce_db(_database_url()) as conn:
            order = _locked_refundable_wechat_order(conn, int(order["id"]))
            amount_total = _validate_refund_request(order, payload)
            currency = str(order.get("currency") or "CNY").strip() or "CNY"
            request_payload = _refund_request_payload(
                order,
                out_refund_no=out_refund_no,
                amount_total=amount_total,
                reason=reason,
                notify_url=refund_notify_url,
            )
            conn.execute(
                """
                INSERT INTO wechat_pay_refunds (
                    order_id, out_trade_no, transaction_id, out_refund_no, reason,
                    refund_amount_total, order_amount_total, currency, status,
                    requested_by, request_payload_json, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'requested', %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    int(order["id"]),
                    order.get("out_trade_no") or "",
                    order["transaction_id"],
                    out_refund_no,
                    reason,
                    amount_total,
                    order["amount_total"],
                    currency,
                    str(payload.get("operator") or "aicrm_next"),
                    Jsonb(request_payload),
                ),
            )
            conn.execute(
                """
                INSERT INTO wechat_pay_order_events (
                    out_trade_no, event_type, transaction_id, trade_state,
                    payload_json, headers_json, created_at
                )
                VALUES (%s, 'refund_request_queued', %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    order.get("out_trade_no") or "",
                    order["transaction_id"],
                    str(order.get("trade_state") or ""),
                    Jsonb({"out_refund_no": out_refund_no, "amount_total": amount_total, "provider_refund_executed": False}),
                    Jsonb({}),
                ),
            )
            effect_job = ExternalEffectService().plan_effect(
                effect_type=PAYMENT_WECHAT_REFUND_REQUEST,
                adapter_name="wechat_payment",
                operation="refund_request",
                target_type="wechat_pay_refund",
                target_id=out_refund_no,
                business_type="commerce_order",
                business_id=str(order.get("out_trade_no") or order_id),
                payload={
                    "request_payload": request_payload,
                    "order_id": order.get("id"),
                    "out_trade_no": order.get("out_trade_no") or "",
                    "out_refund_no": out_refund_no,
                    "transaction_id": order["transaction_id"],
                },
                payload_summary={
                    "order_id": order.get("id"),
                    "out_trade_no": order.get("out_trade_no") or "",
                    "out_refund_no": out_refund_no,
                    "refund_amount_total": amount_total,
                    "currency": currency,
                    "provider_refund_executed": False,
                },
                context=CommandContext(
                    actor_id=str(payload.get("operator") or "aicrm_next"),
                    actor_type="user",
                    trace_id=str(order.get("out_trade_no") or out_refund_no),
                    source_route="commerce.admin_transactions.create_wechat_refund_request",
                ),
                source_module="commerce.admin_transactions",
                idempotency_key=f"wechat-refund-request:{out_refund_no}",
                connection=conn,
            )
            conn.commit()
            updated_order = get_wechat_admin_order(order_id) or order
            return {
                "ok": True,
                "order": updated_order,
                "refund": {
                    "status": "queued",
                    "status_label": _refund_status_label("queued"),
                    "out_refund_no": out_refund_no,
                    "refund_id": "",
                    "provider_refund_executed": False,
                    "external_effect_job_id": effect_job.get("id"),
                },
                "external_effect_job_id": effect_job.get("id"),
                "real_external_call_executed": False,
            }
    else:
        result = build_commerce_repository().request_refund("wechat", str(order_id), request_payload)
        updated_order = _present_order(result["order"])
    return {
        "ok": True,
        "order": updated_order,
        "refund": {
            "status": "requested",
            "status_label": _refund_status_label("requested"),
            "out_refund_no": out_refund_no,
            "provider_refund_executed": False,
        },
    }


def apply_wechat_refund_result(refund_payload: dict[str, Any], *, raw_event: dict[str, Any] | None = None) -> dict[str, Any]:
    if database_mode() != "postgres":
        raise RuntimeError("postgres database is required for refund result sync")
    out_refund_no = str(refund_payload.get("out_refund_no") or "").strip()
    refund_id = str(refund_payload.get("refund_id") or "").strip()
    if not out_refund_no and not refund_id:
        raise ValueError("out_refund_no or refund_id is required")
    status = _normalize_refund_provider_status(refund_payload)
    try:
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError as exc:
        raise RuntimeError("psycopg is required for production transaction admin") from exc

    response_payload = dict(refund_payload)
    if raw_event:
        response_payload["_notify_event"] = dict(raw_event)
    service_period_refund: dict[str, Any] = {}
    order: dict[str, Any] = {}
    with connect_commerce_db(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, o.amount_total AS current_order_amount_total
                FROM wechat_pay_refunds r
                JOIN wechat_pay_orders o ON o.id = r.order_id
                WHERE (%s <> '' AND r.out_refund_no = %s)
                   OR (%s <> '' AND r.refund_id = %s)
                ORDER BY r.id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (out_refund_no, out_refund_no, refund_id, refund_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("refund record not found")
            refund = dict(row)
            previous_status = str(refund.get("status") or "").strip()
            resolved_refund_id = refund_id or str(refund.get("refund_id") or "").strip()
            resolved_out_refund_no = out_refund_no or str(refund.get("out_refund_no") or "").strip()
            refund_amount = _refund_payload_amount(refund_payload, refund.get("refund_amount_total"))
            cur.execute(
                """
                UPDATE wechat_pay_refunds
                SET refund_id = COALESCE(NULLIF(%s, ''), refund_id),
                    status = %s,
                    response_payload_json = %s,
                    error_message = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (resolved_refund_id, status, Jsonb(response_payload), int(refund["id"])),
            )
            order_refund_status = ""
            if status == "SUCCESS" and previous_status != "SUCCESS":
                order_refund_status = build_wechat_pay_order_write_port().apply_refund_success_dbapi(
                    cur,
                    order_id=int(refund["order_id"]),
                    refund_amount=refund_amount,
                )
            cur.execute(
                "SELECT * FROM wechat_pay_orders WHERE id = %s LIMIT 1",
                (int(refund["order_id"]),),
            )
            order = dict(cur.fetchone() or {})
            order_refund_status = str(order.get("refund_status") or order_refund_status)
            cur.execute(
                """
                INSERT INTO wechat_pay_order_events (
                    out_trade_no, event_type, transaction_id, trade_state,
                    payload_json, headers_json, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    str(refund.get("out_trade_no") or refund_payload.get("out_trade_no") or ""),
                    _refund_event_type(status),
                    str(refund.get("transaction_id") or refund_payload.get("transaction_id") or ""),
                    status,
                    Jsonb(
                        {
                            "out_refund_no": resolved_out_refund_no,
                            "refund_id": resolved_refund_id,
                            "wechat_refund_status": status,
                            "previous_refund_status": previous_status,
                            "amount_total": refund_amount,
                            "order_refund_status": order_refund_status,
                        }
                    ),
                    Jsonb({}),
                ),
            )
        if status == "SUCCESS" and order_refund_status == "full_refunded":
            refund_event_payload = {
                "out_refund_no": resolved_out_refund_no,
                "refund_id": resolved_refund_id,
                "status": status,
                "amount_total": refund_amount,
                "order_refund_status": order_refund_status,
                "out_trade_no": str(refund.get("out_trade_no") or refund_payload.get("out_trade_no") or ""),
            }
            request = build_refund_succeeded_event_request(
                refund=refund_event_payload,
                order=order,
                source_route="/api/h5/wechat-pay/refund/notify",
            )
            if request is None:
                raise RuntimeError("refund.succeeded event identity is incomplete")
            outbox = enqueue_transactional_internal_event_outbox(conn, request)
            service_period_refund = {
                "queued": True,
                "event_type": request.event_type,
                "outbox_id": outbox.get("outbox_id"),
                "real_external_call_executed": False,
            }
        conn.commit()
    return {
        "ok": True,
        "refund": {
            "out_refund_no": resolved_out_refund_no,
            "refund_id": resolved_refund_id,
            "status": status,
            "status_label": _refund_status_label(status),
            "previous_status": previous_status,
        },
        "order_refund_status": order_refund_status,
        "service_period_refund": service_period_refund,
        "updated_order_amount": status == "SUCCESS" and previous_status != "SUCCESS",
    }


def handle_wechat_refund_notify(body: str, headers: dict[str, Any]) -> dict[str, Any]:
    try:
        event = json.loads(body or "{}")
    except ValueError as exc:
        raise ValueError("invalid WeChat Pay refund notify JSON") from exc
    event_type = str(event.get("event_type") or "").strip()
    if event_type and not event_type.startswith("REFUND."):
        raise ValueError("not a WeChat Pay refund notification")
    refund_payload = _create_wechat_pay_refund_client().verify_and_decrypt_notification(body=body, headers=headers)
    return apply_wechat_refund_result(refund_payload, raw_event={"id": event.get("id"), "event_type": event_type, "create_time": event.get("create_time")})


def list_wechat_product_options() -> list[dict[str, str]]:
    if database_mode() == "postgres":
        if not _psycopg_available():
            return []
        with connect_commerce_db(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT product_code, COALESCE(NULLIF(name, ''), product_code) AS product_name
                    FROM wechat_pay_products
                    WHERE COALESCE(enabled, TRUE) = TRUE
                    ORDER BY updated_at DESC NULLS LAST, id DESC
                    LIMIT 100
                    """
                )
                return [
                    {"product_code": str(row["product_code"]), "product_name": str(row["product_name"])}
                    for row in cur.fetchall()
                    if row.get("product_code")
                ]
    payload = ListProductsQuery()(limit=100, offset=0)
    return [
        {"product_code": str(item["product_code"]), "product_name": str(item.get("title") or item["product_code"])}
        for item in payload.get("items", [])
        if item.get("product_code")
    ]


def export_orders_csv(filters: dict[str, Any] | None) -> str:
    payload = list_wechat_admin_orders(filters, limit=100, offset=0)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["订单创建时间", "微信单号", "手机号", "unionid", "商品名称", "商品编码", "金额", "状态"])
    for item in payload["items"]:
        writer.writerow(
            [
                item.get("created_at", ""),
                item.get("transaction_id", ""),
                item.get("mobile", ""),
                item.get("unionid", ""),
                item.get("product_name", ""),
                item.get("product_code", ""),
                item.get("amount_yuan", ""),
                item.get("status_label", ""),
            ]
        )
    return output.getvalue()
