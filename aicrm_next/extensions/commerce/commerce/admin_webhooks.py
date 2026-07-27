from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from aicrm_next.shared.runtime import database_mode

from .admin_unified_orders import ROUTE_OWNER, normalize_limit, normalize_offset
from .repo import connect_commerce_db


def _text(value: Any) -> str:
    return str(value or "").strip()


def _format_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return _text(value)


def _payload_preview(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = _text(value)
    return text[:500]


def _connect():
    return connect_commerce_db()


def _source_tables(source: str) -> list[tuple[str, str]]:
    normalized = _text(source or "all").lower()
    mapping = {
        "wechat-pay": [("wechat-pay", "wechat_pay_order_events")],
        "alipay": [("alipay", "alipay_pay_order_events")],
        "all": [("wechat-pay", "wechat_pay_order_events"), ("alipay", "alipay_pay_order_events")],
    }
    return mapping.get(normalized, [])


def _filters(source: dict[str, Any] | None) -> dict[str, str]:
    payload = dict(source or {})
    return {
        "event_type": _text(payload.get("event_type")),
        "order_no": _text(payload.get("order_no") or payload.get("out_trade_no")),
        "transaction_id": _text(payload.get("transaction_id")),
        "status": _text(payload.get("status")),
        "created_from": _text(payload.get("created_from")),
        "created_to": _text(payload.get("created_to")),
    }


def _present(source: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(row.get("id")),
        "source": source,
        "event_type": _text(row.get("event_type")),
        "order_no": _text(row.get("out_trade_no") or row.get("order_no")),
        "out_trade_no": _text(row.get("out_trade_no") or row.get("order_no")),
        "transaction_id": _text(row.get("transaction_id") or row.get("trade_no")),
        "provider_status": _text(row.get("trade_state") or row.get("trade_status") or row.get("status")),
        "payload_preview": _payload_preview(row.get("payload_json") or row.get("payload") or row.get("raw_payload_json")),
        "created_at": _format_time(row.get("created_at")),
    }


def _postgres_events(source: str, table: str, filters: dict[str, str], *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int, list[str]]:
    where = ["1 = 1"]
    params: list[Any] = []
    if filters["event_type"]:
        where.append("COALESCE(event_type, '') ILIKE %s")
        params.append(f"%{filters['event_type']}%")
    if filters["order_no"]:
        where.append("COALESCE(out_trade_no, '') ILIKE %s")
        params.append(f"%{filters['order_no']}%")
    if filters["transaction_id"]:
        where.append("COALESCE(transaction_id, '') ILIKE %s")
        params.append(f"%{filters['transaction_id']}%")
    if filters["status"]:
        where.append("(COALESCE(trade_state, '') ILIKE %s OR COALESCE(trade_status, '') ILIKE %s)")
        params.extend([f"%{filters['status']}%", f"%{filters['status']}%"])
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
                SELECT *
                FROM {table}
                WHERE {clause}
                ORDER BY created_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                tuple([*params, limit, offset]),
            ).fetchall()
        return [_present(source, dict(row)) for row in rows], total, []
    except Exception as exc:
        return [], 0, [f"{source} webhook event table unavailable: {exc}"]


def list_webhook_events(
    *,
    source: str = "all",
    filters: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    page_limit = normalize_limit(limit)
    page_offset = normalize_offset(offset)
    normalized_filters = _filters(filters)
    warnings: list[str] = []
    events: list[dict[str, Any]] = []
    total = 0
    tables = _source_tables(source)
    if not tables and _text(source).lower() not in {"wecom", "customer-automation"}:
        warnings.append(f"source {_text(source)} is not supported in this slice")
    if database_mode() == "postgres":
        for source_name, table in tables:
            rows, count, provider_warnings = _postgres_events(source_name, table, normalized_filters, limit=page_limit, offset=page_offset)
            events.extend(rows)
            total += count
            warnings.extend(provider_warnings)
    return {
        "ok": True,
        "events": events[:page_limit],
        "total": total if database_mode() == "postgres" else len(events),
        "limit": page_limit,
        "offset": page_offset,
        "warnings": warnings,
        "route_owner": ROUTE_OWNER,
        "source_status": "next_admin_webhook_events",
        "fallback_used": False,
    }


def replay_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    dry_run = str(payload.get("dry_run", True)).lower() not in {"0", "false", "no", "off"}
    source = _text(payload.get("source") or "wechat-pay")
    event_id = _text(payload.get("event_id"))
    events_payload = list_webhook_events(source=source, filters={}, limit=100, offset=0)
    event = next((item for item in events_payload["events"] if _text(item.get("id")) == event_id), None)
    if event is None:
        event = {"id": event_id, "source": source} if dry_run and not database_mode() == "postgres" else None
    if event is None:
        raise LookupError("webhook event not found")
    if not dry_run:
        raise RuntimeError("webhook_replay_not_supported")
    return {
        "ok": True,
        "dry_run": True,
        "event": event,
        "replay_result": {
            "mode": "dry_run",
            "message": "webhook replay would use local payment notify logic when explicitly supported",
            "operator": _text(payload.get("operator")),
        },
        "route_owner": ROUTE_OWNER,
        "source_status": "next_admin_webhook_replay",
        "fallback_used": False,
    }
