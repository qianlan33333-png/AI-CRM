from __future__ import annotations

from typing import Any

from aicrm_next.extensions.commerce.commerce.repo import connect_commerce_db
from aicrm_next.platform.shared.runtime import production_data_ready, raw_database_url

from .application import coupon_consistency_counts


_COUNT_KEYS = (
    "paid_order_without_consumed_coupon",
    "consumed_coupon_without_paid_order",
    "closed_order_with_reserved_coupon",
    "order_amount_equation_mismatch",
    "duplicate_active_coupon_redemption",
)


def run_coupon_consistency_check(conn: Any | None = None) -> dict[str, Any]:
    """Run the read-only coupon/order reconciliation counters.

    Fixture mode fails closed instead of silently connecting to a real
    database.  Production schedulers may pass their existing connection so
    this check can share the same read-only monitoring transaction.
    """

    if conn is not None:
        return coupon_consistency_counts(conn)
    if not production_data_ready():
        return {
            "ok": True,
            "skipped": True,
            "reason": "production_data_not_ready",
            "counts": {key: 0 for key in _COUNT_KEYS},
            "total": 0,
        }
    with connect_commerce_db(raw_database_url()) as database_connection:
        return coupon_consistency_counts(database_connection)


coupon_consistency_check = run_coupon_consistency_check


__all__ = ["coupon_consistency_check", "run_coupon_consistency_check"]
