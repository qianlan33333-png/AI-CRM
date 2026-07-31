from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aicrm_next.extensions.commerce.commerce import order_reconciliation
from aicrm_next.extensions.commerce.commerce.coupons import application as coupon_application
from aicrm_next.extensions.commerce.commerce.order_reconciliation import WeChatPayOrderReconciliationWorker
from aicrm_next.channels.integration_gateway.wechat_pay_client import (
    WeChatPayClient,
    WeChatPayClientConfig,
    WeChatPayClientError,
)


class _Cursor:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[tuple[str, tuple]] = []
        self.committed = False
        self.commit_count = 0
        self.closed = False

    def execute(self, query, params=()):
        self.queries.append((query, tuple(params)))
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT * FROM wechat_pay_orders") and "WHERE out_trade_no = %s" in normalized:
            out_trade_no = str(params[0])
            current = next(
                (row for row in self.rows if str(row.get("out_trade_no") or "") == out_trade_no),
                None,
            )
            if current is None:
                return _Cursor(row=None)
            status = str(current.get("status") or "")
            trade_state = str(current.get("trade_state") or "")
            paid_at = current.get("paid_at")
            if status not in {"", "created", "paying", "pending", "provider_unknown"}:
                return _Cursor(row=None)
            if trade_state == "SUCCESS" or paid_at is not None:
                return _Cursor(row=None)
            return _Cursor(row=dict(current))
        if normalized.startswith("SELECT * FROM wechat_pay_orders"):
            return _Cursor(rows=self.rows)
        return _Cursor()

    def commit(self):
        self.committed = True
        self.commit_count += 1

    def rollback(self):
        raise AssertionError("rollback should not be called")

    def close(self):
        self.closed = True


class _FakeClient:
    def __init__(self, states, *, before_query=None):
        self.states = states
        self.before_query = before_query
        self.closed_orders: list[str] = []

    def query_order_by_out_trade_no(self, out_trade_no):
        if self.before_query:
            self.before_query(out_trade_no)
        return {
            "out_trade_no": out_trade_no,
            "trade_state": self.states[out_trade_no],
            "transaction_id": f"tx_{out_trade_no}",
            "amount": {"total": 990, "payer_total": 990},
        }

    def close_order_by_out_trade_no(self, out_trade_no):
        self.closed_orders.append(out_trade_no)
        return {}


class _QueryErrorClient:
    def __init__(self, error: Exception, *, before_query=None):
        self.error = error
        self.before_query = before_query
        self.closed_orders: list[str] = []

    def query_order_by_out_trade_no(self, out_trade_no):
        if self.before_query:
            self.before_query(out_trade_no)
        raise self.error

    def close_order_by_out_trade_no(self, out_trade_no):
        self.closed_orders.append(out_trade_no)
        return {}


def test_wechat_pay_reconciliation_repairs_success_and_closes_unpaid(monkeypatch) -> None:
    conn = _FakeConn(
        [
            {"id": 1, "out_trade_no": "WXP_SUCCESS"},
            {"id": 2, "out_trade_no": "WXP_NOTPAY"},
        ]
    )
    client = _FakeClient({"WXP_SUCCESS": "SUCCESS", "WXP_NOTPAY": "NOTPAY"})
    repaired: list[str] = []

    def fake_apply_transaction(active_conn, transaction, *, source_route):
        assert active_conn is conn
        assert source_route == "wechat_pay_order_reconciliation_worker"
        repaired.append(transaction["out_trade_no"])
        return {"out_trade_no": transaction["out_trade_no"], "status": "paid"}

    monkeypatch.setattr(order_reconciliation, "_apply_transaction", fake_apply_transaction)
    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=False, now=datetime(2026, 7, 4, tzinfo=timezone.utc))

    assert result["ok"] is True
    assert result["repaired_count"] == 1
    assert result["closed_count"] == 1
    assert repaired == ["WXP_SUCCESS"]
    assert client.closed_orders == ["WXP_NOTPAY"]
    assert any("UPDATE wechat_pay_orders" in query and "status = 'closed'" in query for query, _ in conn.queries)
    assert conn.committed is True
    assert conn.commit_count >= 2
    assert conn.closed is True


def test_provider_unknown_candidates_use_propagation_and_confirmation_windows(monkeypatch) -> None:
    monkeypatch.delenv("WECHAT_PAY_RECONCILIATION_PROPAGATION_SECONDS", raising=False)
    now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    rows = [
        {
            "id": 1,
            "out_trade_no": "WXP_PROVIDER_UNKNOWN_RECENT",
            "status": "provider_unknown",
            "created_at": now - timedelta(minutes=1),
        },
        {
            "id": 2,
            "out_trade_no": "WXP_PROVIDER_UNKNOWN_OLD",
            "status": "provider_unknown",
            "created_at": now - timedelta(days=10),
        },
    ]
    conn = _FakeConn(rows)

    candidates = order_reconciliation._select_candidates(
        conn,
        now=now,
        ttl_hours=2,
        window_hours=24,
        limit=10,
    )

    assert [row["out_trade_no"] for row in candidates] == [
        "WXP_PROVIDER_UNKNOWN_RECENT",
        "WXP_PROVIDER_UNKNOWN_OLD",
    ]
    query, params = conn.queries[0]
    normalized = " ".join(query.split())
    assert "COALESCE(provider_unknown_at, updated_at, created_at) <= %s::timestamptz" in normalized
    assert "reconciliation_last_checked_at <= %s::timestamptz" in normalized
    assert "COALESCE(status, '') <> 'provider_unknown'" in normalized
    assert "(created_at >= %s::timestamptz OR coupon_claim_id IS NOT NULL)" in normalized
    assert params == (
        now - timedelta(seconds=120),
        now - timedelta(seconds=120),
        now - timedelta(hours=2),
        now - timedelta(hours=26),
        10,
    )


def test_first_wechat_query_404_records_confirmation_without_releasing_coupon(monkeypatch) -> None:
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    conn = _FakeConn(
        [
            {
                "id": 3,
                "out_trade_no": "WXP_PROVIDER_NOT_FOUND",
                "status": "provider_unknown",
                "coupon_claim_id": 91,
                "reconciliation_not_found_count": 0,
            }
        ]
    )
    client = _QueryErrorClient(
        WeChatPayClientError(
            "order not found",
            status_code=404,
            payload={"code": "ORDER_NOT_EXIST"},
        )
    )
    monkeypatch.setattr(
        coupon_application,
        "release_coupon_for_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("first provider not-found confirmation must retain reservation")
        ),
    )

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=False, now=now)

    assert result["ok"] is True
    assert result["closed_count"] == 0
    assert result["skipped_count"] == 1
    assert result["failed_count"] == 0
    assert result["details"] == [
        {
            "out_trade_no": "WXP_PROVIDER_NOT_FOUND",
            "trade_state": "NOT_FOUND",
            "action": "not_found_confirmation_pending",
        }
    ]
    assert client.closed_orders == []
    assert not any("status = 'closed'" in " ".join(query.split()) for query, _ in conn.queries)
    assert any(
        "reconciliation_not_found_count = COALESCE(reconciliation_not_found_count, 0) + 1"
        in " ".join(query.split())
        for query, _ in conn.queries
    )


def test_second_separated_wechat_query_404_closes_and_releases_coupon(monkeypatch) -> None:
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    conn = _FakeConn(
        [
            {
                "id": 34,
                "out_trade_no": "WXP_PROVIDER_NOT_FOUND_CONFIRMED",
                "status": "provider_unknown",
                "coupon_claim_id": 97,
                "reconciliation_not_found_count": 1,
                "reconciliation_last_checked_at": now - timedelta(minutes=5),
            }
        ]
    )
    client = _QueryErrorClient(
        WeChatPayClientError(
            "order not found",
            status_code=404,
            payload={"code": "ORDER_NOT_EXIST"},
        )
    )
    releases: list[tuple[str, str]] = []

    def fake_release(active_conn, *, out_trade_no, reason):
        assert active_conn is conn
        releases.append((out_trade_no, reason))
        return {"out_trade_no": out_trade_no, "status": "released"}

    monkeypatch.setattr(coupon_application, "release_coupon_for_order", fake_release)

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=False, now=now)

    assert result["ok"] is True
    assert result["closed_count"] == 1
    assert result["failed_count"] == 0
    assert result["details"][0]["action"] == "closed"
    assert releases == [
        (
            "WXP_PROVIDER_NOT_FOUND_CONFIRMED",
            "wechat_pay_reconciliation_provider_not_found",
        )
    ]
    assert any("status = 'closed'" in " ".join(query.split()) for query, _ in conn.queries)


def test_concurrent_second_not_found_inside_propagation_window_does_not_close(monkeypatch) -> None:
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    conn = _FakeConn(
        [
            {
                "id": 35,
                "out_trade_no": "WXP_PROVIDER_NOT_FOUND_TOO_SOON",
                "status": "provider_unknown",
                "coupon_claim_id": 98,
                "reconciliation_not_found_count": 1,
                "reconciliation_last_checked_at": now - timedelta(seconds=15),
            }
        ]
    )
    client = _QueryErrorClient(
        WeChatPayClientError(
            "order not found",
            status_code=404,
            payload={"code": "ORDER_NOT_EXIST"},
        )
    )
    monkeypatch.setattr(
        coupon_application,
        "release_coupon_for_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("confirmations inside one propagation window must retain reservation")
        ),
    )

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=False, now=now)

    assert result["ok"] is True
    assert result["closed_count"] == 0
    assert result["skipped_count"] == 1
    assert result["details"][0]["action"] == "not_found_confirmation_wait"
    assert not any("status = 'closed'" in " ".join(query.split()) for query, _ in conn.queries)


def test_ambiguous_wechat_query_404_keeps_provider_unknown_coupon_reserved(monkeypatch) -> None:
    conn = _FakeConn(
        [
            {
                "id": 31,
                "out_trade_no": "WXP_AMBIGUOUS_404",
                "status": "provider_unknown",
                "coupon_claim_id": 94,
            }
        ]
    )
    client = _QueryErrorClient(
        WeChatPayClientError(
            "gateway route not found",
            status_code=404,
            payload={},
        )
    )
    monkeypatch.setattr(
        coupon_application,
        "release_coupon_for_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ambiguous 404 must retain reservation")),
    )

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=False, now=datetime(2026, 7, 4, tzinfo=timezone.utc))

    assert result["ok"] is False
    assert result["closed_count"] == 0
    assert result["failed_count"] == 1
    assert result["details"] == [
        {
            "out_trade_no": "WXP_AMBIGUOUS_404",
            "action": "failed",
            "error": "WeChatPayClientError",
        }
    ]
    assert client.closed_orders == []
    assert not any("status = 'closed'" in " ".join(query.split()) for query, _ in conn.queries)
    assert any("wechat_pay_reconciliation_error" in str(params[0]) for _query, params in conn.queries if params)


def test_paid_callback_winning_after_unpaid_query_prevents_close_and_coupon_release(monkeypatch) -> None:
    current_order = {
        "id": 32,
        "out_trade_no": "WXP_CALLBACK_WINS",
        "status": "provider_unknown",
        "trade_state": "",
        "paid_at": None,
        "coupon_claim_id": 95,
    }
    conn = _FakeConn([current_order])

    def apply_callback(_out_trade_no: str) -> None:
        current_order.update(
            {
                "status": "paid",
                "trade_state": "SUCCESS",
                "paid_at": datetime(2026, 7, 4, 1, 0, tzinfo=timezone.utc),
            }
        )

    client = _FakeClient({"WXP_CALLBACK_WINS": "NOTPAY"}, before_query=apply_callback)
    monkeypatch.setattr(
        coupon_application,
        "release_coupon_for_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("paid callback must retain consumed coupon")),
    )

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=False, now=datetime(2026, 7, 4, tzinfo=timezone.utc))

    assert result["ok"] is True
    assert result["closed_count"] == 0
    assert result["skipped_count"] == 1
    assert result["details"] == [
        {
            "out_trade_no": "WXP_CALLBACK_WINS",
            "trade_state": "NOTPAY",
            "action": "state_changed",
        }
    ]
    assert client.closed_orders == []
    assert not any("status = 'closed'" in " ".join(query.split()) for query, _ in conn.queries)
    lock_queries = [" ".join(query.split()) for query, _ in conn.queries if "WHERE out_trade_no = %s" in query]
    assert len(lock_queries) == 1
    assert "FOR UPDATE" in lock_queries[0]


def test_paid_callback_winning_before_order_not_exist_recheck_prevents_release(monkeypatch) -> None:
    current_order = {
        "id": 33,
        "out_trade_no": "WXP_CALLBACK_WINS_404",
        "status": "provider_unknown",
        "trade_state": "",
        "paid_at": None,
        "coupon_claim_id": 96,
    }
    conn = _FakeConn([current_order])

    def apply_callback(_out_trade_no: str) -> None:
        current_order.update(
            {
                "status": "paid",
                "trade_state": "SUCCESS",
                "paid_at": datetime(2026, 7, 4, 1, 0, tzinfo=timezone.utc),
            }
        )

    client = _QueryErrorClient(
        WeChatPayClientError(
            "order not found",
            status_code=404,
            payload={"code": "ORDER_NOT_EXIST"},
        ),
        before_query=apply_callback,
    )
    monkeypatch.setattr(
        coupon_application,
        "release_coupon_for_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("paid callback must retain consumed coupon")),
    )

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=False, now=datetime(2026, 7, 4, tzinfo=timezone.utc))

    assert result["ok"] is True
    assert result["closed_count"] == 0
    assert result["skipped_count"] == 1
    assert result["details"] == [
        {
            "out_trade_no": "WXP_CALLBACK_WINS_404",
            "trade_state": "NOT_FOUND",
            "action": "state_changed",
        }
    ]
    assert not any("status = 'closed'" in " ".join(query.split()) for query, _ in conn.queries)


def test_unknown_wechat_query_state_keeps_coupon_reserved_for_later_reconciliation(monkeypatch) -> None:
    conn = _FakeConn(
        [
            {
                "id": 4,
                "out_trade_no": "WXP_PROVIDER_STATE_UNKNOWN",
                "status": "provider_unknown",
                "coupon_claim_id": 92,
            }
        ]
    )
    client = _FakeClient({"WXP_PROVIDER_STATE_UNKNOWN": "UNRECOGNIZED_STATE"})
    monkeypatch.setattr(
        coupon_application,
        "release_coupon_for_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("coupon must remain reserved")),
    )

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=False, now=datetime(2026, 7, 4, tzinfo=timezone.utc))

    assert result["ok"] is True
    assert result["skipped_count"] == 1
    assert result["closed_count"] == 0
    assert result["failed_count"] == 0
    assert result["details"][0]["action"] == "skipped"
    assert client.closed_orders == []
    assert not any("UPDATE wechat_pay_orders" in query for query, _ in conn.queries)


def test_wechat_query_exception_records_error_without_releasing_coupon(monkeypatch) -> None:
    conn = _FakeConn(
        [
            {
                "id": 5,
                "out_trade_no": "WXP_PROVIDER_QUERY_ERROR",
                "status": "provider_unknown",
                "coupon_claim_id": 93,
            }
        ]
    )
    client = _QueryErrorClient(TimeoutError("provider query timed out"))
    monkeypatch.setattr(
        coupon_application,
        "release_coupon_for_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("coupon must remain reserved")),
    )

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=False, now=datetime(2026, 7, 4, tzinfo=timezone.utc))

    assert result["ok"] is False
    assert result["failed_count"] == 1
    assert result["closed_count"] == 0
    assert result["details"][0]["action"] == "failed"
    assert client.closed_orders == []
    update_queries = [" ".join(query.split()) for query, _ in conn.queries if "UPDATE wechat_pay_orders" in query]
    assert len(update_queries) == 1
    assert "SET last_error = %s" in update_queries[0]
    assert "status = 'closed'" not in update_queries[0]


def test_wechat_pay_reconciliation_dry_run_does_not_mutate() -> None:
    conn = _FakeConn([{"id": 1, "out_trade_no": "WXP_SUCCESS"}])
    client = _FakeClient({"WXP_SUCCESS": "SUCCESS"})

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=True, now=datetime(2026, 7, 4, tzinfo=timezone.utc))

    assert result["dry_run"] is True
    assert result["repaired_count"] == 0
    assert result["closed_count"] == 0
    assert client.closed_orders == []
    assert not any("UPDATE wechat_pay_orders" in query for query, _ in conn.queries)


def test_wechat_pay_reconciliation_releases_candidate_lock_before_http_query() -> None:
    conn = _FakeConn([{"id": 1, "out_trade_no": "WXP_SUCCESS"}])
    commit_counts_before_query: list[int] = []
    client = _FakeClient(
        {"WXP_SUCCESS": "SUCCESS"},
        before_query=lambda out_trade_no: commit_counts_before_query.append(conn.commit_count),
    )

    result = WeChatPayOrderReconciliationWorker(
        client_factory=lambda: client,
        connection_factory=lambda: conn,
    ).run_once(limit=10, ttl_hours=2, dry_run=True, now=datetime(2026, 7, 4, tzinfo=timezone.utc))

    assert result["dry_run"] is True
    assert commit_counts_before_query == [1]
    assert conn.commit_count >= 2


def test_wechat_pay_client_close_order_and_trade_bill_request_shapes() -> None:
    requests: list[dict] = []

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

    def fake_request(method, url, data, headers, timeout):
        requests.append({"method": method, "url": url, "data": data.decode("utf-8") if isinstance(data, bytes) else data})
        return Response()

    client = WeChatPayClient(
        WeChatPayClientConfig(
            app_id="app",
            mch_id="mch_123",
            api_v3_key="x" * 32,
            private_key_path="/tmp/missing.pem",
            merchant_serial_no="serial",
            api_base="https://pay.example.test",
        ),
        http_request=fake_request,
    )
    client._merchant_signature = lambda message: "signature"  # type: ignore[method-assign]
    client.verify_response_signature = lambda **_kwargs: None  # type: ignore[method-assign]

    client.close_order_by_out_trade_no("WXP_CLOSE")
    client.request_trade_bill(bill_date="2026-07-04")

    assert requests[0]["method"] == "POST"
    assert requests[0]["url"] == "https://pay.example.test/v3/pay/transactions/out-trade-no/WXP_CLOSE/close"
    assert requests[0]["data"] == '{"mchid":"mch_123"}'
    assert requests[1]["method"] == "GET"
    assert requests[1]["url"] == "https://pay.example.test/v3/bill/tradebill?bill_date=2026-07-04&bill_type=ALL"
