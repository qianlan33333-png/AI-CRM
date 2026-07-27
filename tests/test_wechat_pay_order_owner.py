from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from aicrm_next.extensions.commerce.commerce.wechat_pay_order_write_port import (
    WeChatPayOrderCreate,
    build_wechat_pay_order_write_port,
)


ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, *, row=None, rows=()) -> None:
        self._row = row
        self._rows = list(rows)

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Executor:
    def __init__(self, results=()) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).split()), tuple(params)))
        return self._results.pop(0) if self._results else _Result()


class _CursorExecutor:
    def __init__(self, row) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).split()), tuple(params)))

    def fetchone(self):
        return self.row


def _create_request(out_trade_no: str = "WXP_OWNER_1") -> WeChatPayOrderCreate:
    return WeChatPayOrderCreate(
        out_trade_no=out_trade_no,
        order_source="h5_checkout",
        product_code="owner-product",
        product_name="Owner Product",
        description="Owner Product",
        amount_total=990,
        currency="CNY",
        unionid="union-owner",
        payer_name_snapshot="Owner",
        success_url="/success",
        metadata_json="{}",
        request_meta_json="{}",
        expires_at=datetime.now(timezone.utc),
        client_order_ref="owner-ref",
    )


def test_wechat_pay_order_owner_covers_checkout_and_provider_transitions() -> None:
    port = build_wechat_pay_order_write_port()
    executor = _Executor(
        (
            _Result(row={"id": 1, "status": "created"}),
            _Result(row={"id": 1, "status": "paying"}),
            _Result(row={"id": 1, "status": "paid", "trade_state": "SUCCESS"}),
        )
    )

    created = port.create_dbapi(executor, _create_request())
    paying = port.update_payment_request_dbapi(
        executor,
        out_trade_no="WXP_OWNER_1",
        prepay_id="prepay-owner",
        request_payload_json="{}",
        response_payload_json="{}",
    )
    paid = port.apply_provider_transaction_dbapi(
        executor,
        out_trade_no="WXP_OWNER_1",
        status="paid",
        trade_state="SUCCESS",
        transaction_id="tx-owner",
        bank_type="OTHERS",
        payer_total=990,
        success_time="2026-07-26T01:00:00Z",
        notify_payload_json="{}",
    )

    assert created["status"] == "created"
    assert paying["status"] == "paying"
    assert paid["status"] == "paid"
    assert "INSERT INTO wechat_pay_orders" in executor.calls[0][0]
    assert "provider_unknown_at = NULL" in executor.calls[1][0]
    assert "trade_state = %s" in executor.calls[2][0]


def test_wechat_pay_order_owner_covers_refund_coupon_and_expiration() -> None:
    port = build_wechat_pay_order_write_port()
    refund_cursor = _CursorExecutor({"refund_status": "partial_refunded"})
    refund_status = port.apply_refund_success_dbapi(
        refund_cursor,
        order_id=1,
        refund_amount=100,
    )
    coupon_executor = _Executor((_Result(row={"id": 1, "amount_total": 890}),))
    coupon_order = port.apply_coupon_pricing_dbapi(
        coupon_executor,
        order_id=1,
        out_trade_no="WXP_OWNER_1",
        subtotal=990,
        discount=100,
        payable=890,
        coupon_claim_id=8,
        coupon_snapshot_json="{}",
    )
    expiration_executor = _Executor(
        (_Result(rows=({"id": 2, "status": "closed"},)),)
    )
    closed = port.close_expired_dbapi(
        expiration_executor,
        cutoff=datetime.now(timezone.utc),
        limit=10,
    )

    assert refund_status == "partial_refunded"
    assert coupon_order["amount_total"] == 890
    assert closed == [{"id": 2, "status": "closed"}]
    assert "refunded_amount_total" in refund_cursor.calls[0][0]
    assert "coupon_claim_id" in coupon_executor.calls[0][0]
    assert "FOR UPDATE SKIP LOCKED" in expiration_executor.calls[0][0]


def test_wechat_pay_order_write_sql_is_confined_to_owner_repository() -> None:
    owner = "aicrm_next/extensions/commerce/commerce/wechat_pay_order_write_repository.py"
    markers = (
        "INSERT INTO wechat_pay_orders",
        "UPDATE wechat_pay_orders",
        "DELETE FROM wechat_pay_orders",
    )
    offenders: list[str] = []
    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(marker in source for marker in markers):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative != owner:
            offenders.append(relative)

    assert offenders == []
