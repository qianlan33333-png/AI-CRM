from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from psycopg.types.json import Jsonb

from aicrm_next.integration_ports import (
    WeChatPayClient,
    WeChatPayClientConfig,
    WeChatPayClientError,
    wechat_pay_client_config_from_env,
)
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting
from .wechat_pay_order_write_port import build_wechat_pay_order_write_port
from .order_expiration import pending_order_ttl_hours
from .repo import connect_commerce_db


UNPAID_TRADE_STATES = {"", "NOTPAY", "USERPAYING", "ACCEPT", "PAYERROR"}
TERMINAL_UNPAID_STATES = {"CLOSED", "REVOKED"}
DEFAULT_PROVIDER_UNKNOWN_PROPAGATION_SECONDS = 120
_apply_transaction: Callable[..., dict[str, Any]] | None = None


def provider_unknown_propagation_seconds() -> int:
    try:
        configured = int(
            str(
                managed_runtime_setting(
                    "WECHAT_PAY_RECONCILIATION_PROPAGATION_SECONDS"
                )
                or ""
            ).strip()
        )
    except (TypeError, ValueError):
        configured = DEFAULT_PROVIDER_UNKNOWN_PROPAGATION_SECONDS
    return max(30, min(configured, 3600))


class WeChatPayOrderReconciliationWorker:
    def __init__(
        self,
        *,
        client_factory: Callable[[], WeChatPayClient] | None = None,
        connection_factory: Callable[[], Any] | None = None,
        transaction_applier: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.client_factory = client_factory or (lambda: WeChatPayClient(wechat_pay_client_config_from_env()))
        self.connection_factory = connection_factory or connect_commerce_db
        self.transaction_applier = transaction_applier

    def run_once(
        self,
        *,
        limit: int = 100,
        ttl_hours: int | None = None,
        window_hours: int = 24,
        dry_run: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        source_now = _utc(now)
        conn = self.connection_factory()
        client = self.client_factory()
        scanned = 0
        repaired = 0
        closed = 0
        skipped = 0
        failed = 0
        details: list[dict[str, Any]] = []
        try:
            candidates = _select_candidates(
                conn,
                now=source_now,
                ttl_hours=ttl_hours or pending_order_ttl_hours(),
                window_hours=window_hours,
                limit=limit,
            )
            conn.commit()
            for order in candidates:
                scanned += 1
                out_trade_no = str(order.get("out_trade_no") or "").strip()
                if not out_trade_no:
                    skipped += 1
                    continue
                try:
                    transaction = client.query_order_by_out_trade_no(out_trade_no)
                    trade_state = str(transaction.get("trade_state") or "").strip().upper()
                    if dry_run:
                        details.append({"out_trade_no": out_trade_no, "trade_state": trade_state, "action": "preview"})
                        continue
                    if trade_state == "SUCCESS":
                        transaction_applier = self.transaction_applier or _apply_transaction
                        if transaction_applier is None:
                            raise RuntimeError("payment transaction applier composition unavailable")
                        transaction_applier(conn, transaction, source_route="wechat_pay_order_reconciliation_worker")
                        _insert_order_event(
                            conn,
                            out_trade_no=out_trade_no,
                            event_type="reconciliation_payment_repaired",
                            transaction=transaction,
                        )
                        conn.commit()
                        repaired += 1
                        details.append({"out_trade_no": out_trade_no, "trade_state": trade_state, "action": "repaired"})
                    elif trade_state in UNPAID_TRADE_STATES:
                        locked_order = _lock_reconcilable_order(conn, out_trade_no=out_trade_no)
                        if locked_order is None:
                            conn.commit()
                            skipped += 1
                            details.append(
                                {
                                    "out_trade_no": out_trade_no,
                                    "trade_state": trade_state,
                                    "action": "state_changed",
                                }
                            )
                            continue
                        client.close_order_by_out_trade_no(out_trade_no)
                        _mark_order_closed(conn, out_trade_no=out_trade_no, reason="wechat_pay_reconciliation_closed_unpaid")
                        if locked_order.get("coupon_claim_id"):
                            from aicrm_next.extensions.commerce.commerce.coupons.application import release_coupon_for_order

                            release_coupon_for_order(conn, out_trade_no=out_trade_no, reason="wechat_pay_reconciliation_closed_unpaid")
                        _insert_order_event(
                            conn,
                            out_trade_no=out_trade_no,
                            event_type="reconciliation_order_closed",
                            transaction=transaction,
                        )
                        conn.commit()
                        closed += 1
                        details.append({"out_trade_no": out_trade_no, "trade_state": trade_state, "action": "closed"})
                    elif trade_state in TERMINAL_UNPAID_STATES:
                        locked_order = _lock_reconcilable_order(conn, out_trade_no=out_trade_no)
                        if locked_order is None:
                            conn.commit()
                            skipped += 1
                            details.append(
                                {
                                    "out_trade_no": out_trade_no,
                                    "trade_state": trade_state,
                                    "action": "state_changed",
                                }
                            )
                            continue
                        _mark_order_closed(conn, out_trade_no=out_trade_no, reason="wechat_pay_reconciliation_terminal_unpaid")
                        if locked_order.get("coupon_claim_id"):
                            from aicrm_next.extensions.commerce.commerce.coupons.application import release_coupon_for_order

                            release_coupon_for_order(conn, out_trade_no=out_trade_no, reason="wechat_pay_reconciliation_terminal_unpaid")
                        conn.commit()
                        closed += 1
                        details.append({"out_trade_no": out_trade_no, "trade_state": trade_state, "action": "marked_closed"})
                    else:
                        skipped += 1
                        details.append({"out_trade_no": out_trade_no, "trade_state": trade_state, "action": "skipped"})
                except Exception as exc:  # pragma: no cover - defensive per-order isolation
                    provider_not_found = _is_provider_order_not_exist(exc)
                    if provider_not_found and dry_run:
                        skipped += 1
                        details.append(
                            {
                                "out_trade_no": out_trade_no,
                                "trade_state": "NOT_FOUND",
                                "action": (
                                    "preview_close"
                                    if int(order.get("reconciliation_not_found_count") or 0) >= 1
                                    else "preview_not_found_confirmation"
                                ),
                            }
                        )
                        continue
                    if provider_not_found:
                        try:
                            locked_order = _lock_reconcilable_order(conn, out_trade_no=out_trade_no)
                            if locked_order is None:
                                conn.commit()
                                skipped += 1
                                details.append(
                                    {
                                        "out_trade_no": out_trade_no,
                                        "trade_state": "NOT_FOUND",
                                        "action": "state_changed",
                                    }
                                )
                                continue
                            confirmation_count = int(
                                locked_order.get("reconciliation_not_found_count") or 0
                            )
                            last_checked_at = _datetime_or_none(
                                locked_order.get("reconciliation_last_checked_at")
                            )
                            confirmation_cutoff = source_now - timedelta(
                                seconds=provider_unknown_propagation_seconds()
                            )
                            if confirmation_count < 1:
                                _record_provider_not_found_confirmation(
                                    conn,
                                    out_trade_no=out_trade_no,
                                    checked_at=source_now,
                                )
                                conn.commit()
                                skipped += 1
                                details.append(
                                    {
                                        "out_trade_no": out_trade_no,
                                        "trade_state": "NOT_FOUND",
                                        "action": "not_found_confirmation_pending",
                                    }
                                )
                                continue
                            if last_checked_at is None or last_checked_at > confirmation_cutoff:
                                conn.commit()
                                skipped += 1
                                details.append(
                                    {
                                        "out_trade_no": out_trade_no,
                                        "trade_state": "NOT_FOUND",
                                        "action": "not_found_confirmation_wait",
                                    }
                                )
                                continue
                            _mark_order_closed(
                                conn,
                                out_trade_no=out_trade_no,
                                reason="wechat_pay_reconciliation_provider_not_found",
                            )
                            if locked_order.get("coupon_claim_id"):
                                from aicrm_next.extensions.commerce.commerce.coupons.application import release_coupon_for_order

                                release_coupon_for_order(
                                    conn,
                                    out_trade_no=out_trade_no,
                                    reason="wechat_pay_reconciliation_provider_not_found",
                                )
                            conn.commit()
                            closed += 1
                            details.append(
                                {
                                    "out_trade_no": out_trade_no,
                                    "trade_state": "NOT_FOUND",
                                    "action": "closed",
                                }
                            )
                            continue
                        except Exception as repair_exc:  # pragma: no cover - defensive repair isolation
                            exc = repair_exc
                    failed += 1
                    if not dry_run:
                        _mark_order_reconciliation_error(conn, out_trade_no=out_trade_no, error=str(exc))
                        conn.commit()
                    details.append({"out_trade_no": out_trade_no, "action": "failed", "error": type(exc).__name__})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            "ok": failed == 0,
            "dry_run": dry_run,
            "scanned_count": scanned,
            "repaired_count": repaired,
            "closed_count": closed,
            "skipped_count": skipped,
            "failed_count": failed,
            "details": details,
            "source_status": "wechat_pay_order_reconciliation_worker",
            "real_external_call_executed": not dry_run and scanned > 0,
        }


def request_wechat_pay_trade_bill(
    *,
    bill_date: str,
    bill_type: str = "ALL",
    client: WeChatPayClient | None = None,
    config: WeChatPayClientConfig | None = None,
) -> dict[str, Any]:
    active_client = client or WeChatPayClient(config or wechat_pay_client_config_from_env())
    return active_client.request_trade_bill(bill_date=bill_date, bill_type=bill_type)


def _select_candidates(
    conn: Any,
    *,
    now: datetime,
    ttl_hours: int,
    window_hours: int,
    limit: int,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(hours=max(1, ttl_hours))
    oldest = now - timedelta(hours=max(1, ttl_hours) + max(1, window_hours))
    provider_cutoff = now - timedelta(seconds=provider_unknown_propagation_seconds())
    rows = conn.execute(
        """
        SELECT *
        FROM wechat_pay_orders
        WHERE COALESCE(status, '') IN ('created', 'paying', 'pending', 'provider_unknown', '')
          AND COALESCE(trade_state, '') <> 'SUCCESS'
          AND paid_at IS NULL
          AND (
                (
                    status = 'provider_unknown'
                    AND COALESCE(provider_unknown_at, updated_at, created_at) <= %s::timestamptz
                    AND (
                        reconciliation_last_checked_at IS NULL
                        OR reconciliation_last_checked_at <= %s::timestamptz
                    )
                )
                OR (
                    COALESCE(status, '') <> 'provider_unknown'
                    AND created_at <= %s::timestamptz
                    AND (created_at >= %s::timestamptz OR coupon_claim_id IS NOT NULL)
                )
          )
        ORDER BY created_at ASC, id ASC
        LIMIT %s
        FOR UPDATE SKIP LOCKED
        """,
        (
            provider_cutoff,
            provider_cutoff,
            cutoff,
            oldest,
            max(1, min(int(limit or 100), 500)),
        ),
    ).fetchall()
    return [dict(row) for row in rows or []]


def _mark_order_closed(conn: Any, *, out_trade_no: str, reason: str) -> None:
    build_wechat_pay_order_write_port().close_reconcilable_dbapi(
        conn,
        out_trade_no=out_trade_no,
        reason=reason,
    )


def _lock_reconcilable_order(conn: Any, *, out_trade_no: str) -> dict[str, Any] | None:
    """Re-read the order under lock after the provider query.

    The candidate lock is intentionally released before the network request.  Any
    callback may therefore win that interval.  A close/release path must acquire
    this lock and prove the order is still unpaid and locally reconcilable before
    mutating it.
    """

    row = conn.execute(
        """
        SELECT *
        FROM wechat_pay_orders
        WHERE out_trade_no = %s
          AND COALESCE(status, '') IN ('created', 'paying', 'pending', 'provider_unknown', '')
          AND COALESCE(trade_state, '') <> 'SUCCESS'
          AND paid_at IS NULL
        FOR UPDATE
        """,
        (out_trade_no,),
    ).fetchone()
    return dict(row) if row else None


def _is_provider_order_not_exist(exc: Exception) -> bool:
    if not isinstance(exc, WeChatPayClientError) or int(exc.status_code or 0) != 404:
        return False
    return str(exc.payload.get("code") or "").strip().upper() == "ORDER_NOT_EXIST"


def _mark_order_reconciliation_error(conn: Any, *, out_trade_no: str, error: str) -> None:
    build_wechat_pay_order_write_port().mark_reconciliation_error_dbapi(
        conn,
        out_trade_no=out_trade_no,
        last_error=f"wechat_pay_reconciliation_error: {error}",
    )


def _record_provider_not_found_confirmation(
    conn: Any,
    *,
    out_trade_no: str,
    checked_at: datetime,
) -> None:
    build_wechat_pay_order_write_port().record_provider_not_found_dbapi(
        conn,
        out_trade_no=out_trade_no,
        checked_at=checked_at,
    )


def _insert_order_event(conn: Any, *, out_trade_no: str, event_type: str, transaction: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO wechat_pay_order_events (
            out_trade_no, event_type, transaction_id, trade_state, payload_json, headers_json, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (
            out_trade_no,
            event_type,
            str(transaction.get("transaction_id") or ""),
            str(transaction.get("trade_state") or ""),
            Jsonb(transaction),
            Jsonb({}),
        ),
    )


def _utc(value: datetime | None) -> datetime:
    source = value or datetime.now(timezone.utc)
    if source.tzinfo is None:
        source = source.replace(tzinfo=timezone.utc)
    return source.astimezone(timezone.utc)


def _datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return _utc(value)
