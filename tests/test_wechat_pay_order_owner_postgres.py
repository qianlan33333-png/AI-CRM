from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.extensions.commerce.commerce.wechat_pay_order_write_port import (
    WeChatPayOrderCreate,
    build_wechat_pay_order_write_port,
)


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _request(out_trade_no: str) -> WeChatPayOrderCreate:
    return WeChatPayOrderCreate(
        out_trade_no=out_trade_no,
        order_source="owner_test",
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
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        client_order_ref=out_trade_no,
    )


def test_wechat_pay_order_owner_checkout_callback_refund_and_expiration(next_pg_schema) -> None:
    paid_trade_no = f"WXP_OWNER_PAID_{uuid4().hex}"
    expired_trade_no = f"WXP_OWNER_EXPIRED_{uuid4().hex}"
    port = build_wechat_pay_order_write_port()
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        paid = port.create_dbapi(connection, _request(paid_trade_no))
        expired = port.create_dbapi(connection, _request(expired_trade_no))
        paying = port.update_payment_request_dbapi(
            connection,
            out_trade_no=paid_trade_no,
            prepay_id="prepay-owner",
            request_payload_json="{}",
            response_payload_json="{}",
        )
        settled = port.apply_provider_transaction_dbapi(
            connection,
            out_trade_no=paid_trade_no,
            status="paid",
            trade_state="SUCCESS",
            transaction_id="tx-owner",
            bank_type="OTHERS",
            payer_total=990,
            success_time="2026-07-26T01:00:00Z",
            notify_payload_json="{}",
        )
        refund_status = port.apply_refund_success_dbapi(
            connection.cursor(row_factory=dict_row),
            order_id=int(paid["id"]),
            refund_amount=100,
        )
        closed = port.close_expired_dbapi(
            connection,
            cutoff=datetime.now(timezone.utc) + timedelta(hours=3),
            limit=10,
            out_trade_no=expired_trade_no,
        )
        connection.commit()

    assert int(expired["id"]) > 0
    assert paying["status"] == "paying"
    assert settled["status"] == "paid" and settled["trade_state"] == "SUCCESS"
    assert refund_status == "partial_refunded"
    assert [row["out_trade_no"] for row in closed] == [expired_trade_no]
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        rows = {
            row["out_trade_no"]: dict(row)
            for row in connection.execute(
                """
                SELECT out_trade_no, status, trade_state, refunded_amount_total, refund_status
                FROM wechat_pay_orders
                WHERE out_trade_no IN (%s, %s)
                """,
                (paid_trade_no, expired_trade_no),
            ).fetchall()
        }
    assert rows[paid_trade_no] == {
        "out_trade_no": paid_trade_no,
        "status": "paid",
        "trade_state": "SUCCESS",
        "refunded_amount_total": 100,
        "refund_status": "partial_refunded",
    }
    assert rows[expired_trade_no]["status"] == "closed"
    assert rows[expired_trade_no]["trade_state"] == "CLOSED"
