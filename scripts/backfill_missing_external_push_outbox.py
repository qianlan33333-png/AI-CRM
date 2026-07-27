from __future__ import annotations

import argparse
from typing import Any

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.extensions.commerce.commerce.external_push_outbox import (  # noqa: E402
    DEFAULT_TENANT_ID,
    EVENT_TRANSACTION_PAID,
    AGGREGATE_TYPE_WECHAT_PAY_ORDER,
    build_transaction_paid_outbox_payload,
    enqueue_transaction_paid_outbox,
    resolve_product_for_order,
)
from aicrm_next.shared.runtime import raw_database_url  # noqa: E402


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(raw_database_url(), row_factory=dict_row)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _candidate_orders(conn: Any, *, order_id: int | None, product_code: str, limit: int) -> list[dict[str, Any]]:
    where = ["(o.status = 'paid' OR o.trade_state = 'SUCCESS')"]
    params: list[Any] = []
    if order_id:
        where.append("o.id = %s")
        params.append(int(order_id))
    if product_code:
        where.append("o.product_code = %s")
        params.append(product_code)
    rows = conn.execute(
        f"""
        SELECT o.*
        FROM wechat_pay_orders o
        WHERE {" AND ".join(where)}
        ORDER BY o.paid_at DESC NULLS LAST, o.updated_at DESC NULLS LAST, o.id DESC
        LIMIT %s
        """,
        (*params, max(1, min(int(limit), 1000))),
    ).fetchall()
    return [dict(row) for row in rows]


def _enabled_config_for_product(conn: Any, product_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM external_push_config
        WHERE tenant_id = %s
          AND target_type = 'product'
          AND target_id = %s
          AND event_type = %s
          AND enabled = TRUE
        LIMIT 1
        """,
        (DEFAULT_TENANT_ID, str(int(product_id)), EVENT_TRANSACTION_PAID),
    ).fetchone()
    return dict(row) if row else None


def _outbox_exists(conn: Any, order_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM domain_event_outbox
        WHERE tenant_id = %s
          AND event_type = %s
          AND aggregate_type = %s
          AND aggregate_id = %s
        LIMIT 1
        """,
        (DEFAULT_TENANT_ID, EVENT_TRANSACTION_PAID, AGGREGATE_TYPE_WECHAT_PAY_ORDER, str(int(order_id))),
    ).fetchone()
    return bool(row)


def _scan_and_backfill(conn: Any, *, order_id: int | None, product_code: str, limit: int, dry_run: bool) -> dict[str, Any]:
    candidates = _candidate_orders(conn, order_id=order_id, product_code=product_code, limit=limit)
    items: list[dict[str, Any]] = []
    inserted = 0
    for order in candidates:
        product = resolve_product_for_order(conn, order)
        product_id = int(product.get("id") or 0)
        item = {
            "order_id": int(order.get("id") or 0),
            "out_trade_no": _text(order.get("out_trade_no")),
            "product_code": _text(order.get("product_code")),
            "product_id": product_id,
            "action": "",
        }
        if product_id <= 0:
            item["action"] = "skipped_product_not_found"
        elif not _enabled_config_for_product(conn, product_id):
            item["action"] = "skipped_enabled_config_not_found"
        elif _outbox_exists(conn, int(order["id"])):
            item["action"] = "skipped_outbox_exists"
        elif dry_run:
            item["action"] = "would_insert"
            item["payload"] = build_transaction_paid_outbox_payload(order, product)
        else:
            outbox = enqueue_transaction_paid_outbox(conn, order)
            item["action"] = "inserted" if outbox else "deduped"
            item["outbox_id"] = int((outbox or {}).get("id") or 0)
            inserted += 1 if outbox else 0
        items.append(item)
    if not dry_run:
        conn.commit()
    return {
        "ok": True,
        "dry_run": dry_run,
        "scanned_count": len(candidates),
        "inserted_count": inserted,
        "items": items,
        "next_step": "run canonical commerce reconciliation and internal/external effect workers",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill missing transaction.paid external-push outbox events.")
    parser.add_argument("--order-id", type=int)
    parser.add_argument("--product-code", default="")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with _connect() as conn:
        payload = _scan_and_backfill(
            conn,
            order_id=args.order_id,
            product_code=_text(args.product_code),
            limit=args.limit,
            dry_run=bool(args.dry_run),
        )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
