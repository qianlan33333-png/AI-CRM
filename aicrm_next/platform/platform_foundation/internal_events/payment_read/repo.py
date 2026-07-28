from __future__ import annotations

from typing import Any

from aicrm_next.platform.shared.runtime import production_data_ready, raw_database_url


def read_wechat_pay_order_for_payment_event(*, lookup: str, aggregate_id: str) -> dict[str, Any]:
    if not production_data_ready():
        return {}
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(raw_database_url(), row_factory=dict_row) as conn:
            if lookup:
                row = conn.execute("SELECT * FROM wechat_pay_orders WHERE out_trade_no = %s LIMIT 1", (lookup,)).fetchone()
                if row:
                    return dict(row)
            if aggregate_id:
                row = conn.execute(
                    "SELECT * FROM wechat_pay_orders WHERE id::text = %s OR out_trade_no = %s LIMIT 1",
                    (aggregate_id, aggregate_id),
                ).fetchone()
                if row:
                    return dict(row)
    except Exception as exc:
        raise RuntimeError("authoritative payment order read failed") from exc
    return {}
