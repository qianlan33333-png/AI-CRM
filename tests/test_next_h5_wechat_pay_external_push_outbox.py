from __future__ import annotations

from aicrm_next.extensions.commerce.commerce import external_push_outbox


class FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self):
        self.queries: list[tuple[str, tuple]] = []
        self.product_row = {"id": 3, "product_code": "subscription_trial_month", "name": "Next H5 外推商品", "amount_total": 990}
        self.outbox_row = {
            "id": 9,
            "tenant_id": "aicrm",
            "event_type": "transaction.paid",
            "aggregate_type": "wechat_pay_order",
            "aggregate_id": "7",
            "payload": {},
            "status": "pending",
        }

    def execute(self, query, params):
        self.queries.append((query, params))
        if "FROM wechat_pay_products" in query:
            return FakeCursor(self.product_row)
        if "INSERT INTO domain_event_outbox" in query:
            row = dict(self.outbox_row)
            row["aggregate_id"] = params[3]
            row["payload"] = params[4]
            return FakeCursor(row)
        raise AssertionError(query)


def _paid_order(**overrides) -> dict:
    payload = {
        "id": 7,
        "out_trade_no": "WXP_NEXT_NOTIFY_OUTBOX",
        "product_code": "subscription_trial_month",
        "product_name": "Next H5 外推商品",
        "amount_total": 990,
        "payer_total": 990,
        "status": "paid",
        "trade_state": "SUCCESS",
        "external_userid": "wm_next_h5",
        "userid_snapshot": "tester",
        "paid_at": "2026-06-05T02:54:47+00:00",
    }
    payload.update(overrides)
    return payload


def test_next_h5_transaction_paid_outbox_payload_uses_next_helper():
    payload = external_push_outbox.build_transaction_paid_outbox_payload(
        _paid_order(),
        {"id": 3, "product_code": "subscription_trial_month"},
    )

    assert payload == {
        "order_id": "7",
        "product_id": "3",
        "product_code": "subscription_trial_month",
        "tenant_id": "aicrm",
        "buyer_id": "wm_next_h5",
        "paid_amount": 990,
        "paid_at": "2026-06-05T02:54:47Z",
        "pay_channel": "wechat",
    }


def test_next_h5_enqueue_transaction_paid_outbox_is_idempotent_contract():
    conn = FakeConn()

    row = external_push_outbox.enqueue_transaction_paid_outbox(conn, _paid_order())

    assert row["tenant_id"] == "aicrm"
    assert row["event_type"] == "transaction.paid"
    assert row["aggregate_type"] == "wechat_pay_order"
    assert row["aggregate_id"] == "7"
    product_params = conn.queries[0][1]
    assert "subscription_trial_month" in product_params[0]
    insert_params = conn.queries[1][1]
    assert insert_params[:4] == ("aicrm", "transaction.paid", "wechat_pay_order", "7")


def test_next_h5_enqueue_skips_unpaid_orders():
    conn = FakeConn()

    row = external_push_outbox.enqueue_transaction_paid_outbox(conn, _paid_order(status="paying", trade_state="NOTPAY"))

    assert row is None
    assert conn.queries == []
