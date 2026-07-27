from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from aicrm_next.shared.runtime import production_data_ready
from aicrm_next.shared.runtime_settings import managed_runtime_setting

from .repo import connect_commerce_db
from .wechat_pay_order_write_port import build_wechat_pay_order_write_port

DEFAULT_PENDING_ORDER_TTL_HOURS = 2
MAX_EXPIRE_BATCH_SIZE = 500


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pending_order_ttl_hours() -> int:
    configured = _int(
        managed_runtime_setting("WECHAT_PAY_PENDING_ORDER_TTL_HOURS"),
        DEFAULT_PENDING_ORDER_TTL_HOURS,
    )
    return max(1, min(configured, 24))


def pending_order_expires_at(*, now: datetime | None = None, ttl_hours: int | None = None) -> datetime:
    source = now or datetime.now(timezone.utc)
    if source.tzinfo is None:
        source = source.replace(tzinfo=timezone.utc)
    return source.astimezone(timezone.utc) + timedelta(hours=ttl_hours or pending_order_ttl_hours())


def pending_order_expires_at_text(*, now: datetime | None = None, ttl_hours: int | None = None) -> str:
    return pending_order_expires_at(now=now, ttl_hours=ttl_hours).strftime("%Y-%m-%dT%H:%M:%SZ")


def _close_expired_with_conn(
    conn: Any,
    *,
    now: datetime | None = None,
    ttl_hours: int | None = None,
    limit: int = 200,
    out_trade_no: str = "",
) -> list[dict[str, Any]]:
    source_now = now or datetime.now(timezone.utc)
    if source_now.tzinfo is None:
        source_now = source_now.replace(tzinfo=timezone.utc)
    cutoff = source_now.astimezone(timezone.utc) - timedelta(hours=ttl_hours or pending_order_ttl_hours())
    page_size = max(1, min(_int(limit, 200), MAX_EXPIRE_BATCH_SIZE))
    return build_wechat_pay_order_write_port().close_expired_dbapi(
        conn,
        cutoff=cutoff,
        limit=page_size,
        out_trade_no=out_trade_no,
    )


def close_expired_wechat_pay_orders(
    *,
    conn: Any | None = None,
    now: datetime | None = None,
    ttl_hours: int | None = None,
    limit: int = 200,
    out_trade_no: str = "",
) -> dict[str, Any]:
    ttl = ttl_hours or pending_order_ttl_hours()
    if conn is None and not production_data_ready():
        return {
            "ok": True,
            "closed_count": 0,
            "orders": [],
            "ttl_hours": ttl,
            "source_status": "fixture_noop",
            "real_external_call_executed": False,
        }
    owns_connection = conn is None
    active_conn = conn or connect_commerce_db()
    try:
        rows = _close_expired_with_conn(active_conn, now=now, ttl_hours=ttl, limit=limit, out_trade_no=out_trade_no)
        if owns_connection:
            active_conn.commit()
        return {
            "ok": True,
            "closed_count": len(rows),
            "orders": rows,
            "ttl_hours": ttl,
            "source_status": "next_wechat_pay_order_expiration",
            "real_external_call_executed": False,
        }
    except Exception:
        if owns_connection:
            active_conn.rollback()
        raise
    finally:
        if owns_connection:
            active_conn.close()
