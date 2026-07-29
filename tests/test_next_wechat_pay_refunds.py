from __future__ import annotations

import sys
import types

from aicrm_next.extensions.commerce.commerce import admin_transactions
from aicrm_next.extensions.commerce.commerce import admin_transaction_detail
from aicrm_next.extensions.commerce.commerce import api as commerce_api


def _paid_order(**overrides):
    order = {
        "id": 19,
        "out_trade_no": "WXP_REAL_REFUND",
        "transaction_id": "420000REALNEXT",
        "amount_total": 6900,
        "currency": "CNY",
        "trade_state": "SUCCESS",
        "can_refund": True,
        "refundable_amount_total": 6900,
    }
    order.update(overrides)
    return order


def _install_fake_psycopg(monkeypatch, *, locked_order: dict | None = None):
    executed: list[tuple[str, tuple]] = []

    class FakeResult:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            executed.append((sql, tuple(params)))

    class FakeConnection:
        def __init__(self):
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor()

        def execute(self, sql, params=()):
            executed.append((sql, tuple(params)))
            if "SELECT id FROM wechat_pay_orders" in sql and "FOR UPDATE" in sql:
                return FakeResult({"id": int((locked_order or _paid_order())["id"])})
            if "active_refund_amount_total" in sql:
                return FakeResult(dict(locked_order or _paid_order(), active_refund_amount_total=0))
            return FakeResult()

        def commit(self):
            self.commits += 1

    connections: list[FakeConnection] = []

    def connect(*args, **kwargs):
        conn = FakeConnection()
        connections.append(conn)
        return conn

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    psycopg_types = types.ModuleType("psycopg.types")
    json_module = types.ModuleType("psycopg.types.json")
    json_module.Jsonb = lambda value: value
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)
    monkeypatch.setitem(sys.modules, "psycopg.types", psycopg_types)
    monkeypatch.setitem(sys.modules, "psycopg.types.json", json_module)
    return executed, connections


def test_next_postgres_refund_queues_external_effect_without_direct_wechat_pay(monkeypatch):
    order = _paid_order()
    executed, connections = _install_fake_psycopg(monkeypatch, locked_order=order)
    calls: list[dict] = []
    planned: list[dict] = []

    class FakeClient:
        def create_refund(self, payload):
            calls.append(payload)
            return {
                "status": "SUCCESS",
                "refund_id": "503000000020260604",
                "out_refund_no": payload["out_refund_no"],
            }

    monkeypatch.setattr(admin_transactions, "database_mode", lambda: "postgres")
    monkeypatch.setattr(admin_transactions, "_database_url", lambda: "postgresql://test/test")
    monkeypatch.setattr(admin_transactions, "get_wechat_admin_order", lambda order_id: order)
    monkeypatch.setattr(admin_transactions, "_create_wechat_pay_refund_client", lambda: FakeClient())
    monkeypatch.setattr(
        admin_transactions.ExternalEffectService,
        "plan_effect",
        lambda self, **kwargs: planned.append(kwargs) or {"id": 42},
    )

    result = admin_transactions.create_wechat_refund_request(
        "19",
        {
            "refund_amount_total": 6900,
            "reason": "客户主动申请退款",
            "transaction_id_confirmation": "420000REALNEXT",
            "checked": True,
            "operator": "tester",
        },
    )

    assert result["ok"] is True
    assert result["refund"]["status"] == "queued"
    assert result["refund"]["provider_refund_executed"] is False
    assert result["refund"]["external_effect_job_id"] == 42
    assert result["order"] == order
    assert calls == []
    assert planned[0]["connection"] is connections[0]
    assert planned[0]["idempotency_key"].startswith("wechat-refund-request:")
    sql_text = "\n".join(sql for sql, _params in executed)
    assert "INSERT INTO wechat_pay_refunds" in sql_text
    assert "refund_request_queued" in sql_text
    assert "INSERT INTO wechat_pay_order_events" in sql_text
    assert connections[0].commits == 1


def test_next_postgres_refund_does_not_call_wechat_pay_when_client_would_reject(monkeypatch):
    order = _paid_order(amount_total=9900, refundable_amount_total=9900)
    executed, connections = _install_fake_psycopg(monkeypatch, locked_order=order)
    planned: list[dict] = []

    class FakeClient:
        def create_refund(self, payload):
            raise RuntimeError("wechat unavailable")

    monkeypatch.setattr(admin_transactions, "database_mode", lambda: "postgres")
    monkeypatch.setattr(admin_transactions, "_database_url", lambda: "postgresql://test/test")
    monkeypatch.setattr(admin_transactions, "get_wechat_admin_order", lambda order_id: order)
    monkeypatch.setattr(admin_transactions, "_create_wechat_pay_refund_client", lambda: FakeClient())
    monkeypatch.setattr(
        admin_transactions.ExternalEffectService,
        "plan_effect",
        lambda self, **kwargs: planned.append(kwargs) or {"id": 43},
    )

    result = admin_transactions.create_wechat_refund_request(
        "19",
        {
            "refund_amount_total": 9900,
            "reason": "客户主动申请退款",
            "transaction_id_confirmation": "420000REALNEXT",
            "checked": True,
        },
    )

    assert result["ok"] is True
    assert result["refund"]["status"] == "queued"
    assert result["refund"]["provider_refund_executed"] is False
    assert planned[0]["connection"] is connections[0]
    sql_text = "\n".join(sql for sql, _params in executed)
    assert "INSERT INTO wechat_pay_refunds" in sql_text
    assert "refund_request_queued" in sql_text
    assert "'refund_failed'" not in sql_text
    assert connections[0].commits == 1


def test_wechat_refund_processing_amount_excludes_terminal_external_effect_jobs():
    list_select = admin_transactions._postgres_order_select()
    detail_select = admin_transaction_detail._postgres_order_select("wechat")

    for sql in (list_select, detail_select):
        assert "FROM external_effect_job j" in sql
        assert "j.target_type = 'wechat_pay_refund'" in sql
        assert "failed_terminal" in sql
        assert "blocked" in sql
        assert "cancelled" in sql
        assert "expired" in sql
        assert "LOWER(COALESCE(r.status" in sql


def _install_fetching_fake_psycopg(monkeypatch, refund_row: dict):
    executed: list[tuple[str, tuple]] = []

    class FakeCursor:
        def __init__(self):
            self.last_sql = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            self.last_sql = sql
            executed.append((sql, tuple(params)))

        def fetchone(self):
            if "SELECT r.*" in self.last_sql:
                return dict(refund_row)
            if "RETURNING refund_status" in self.last_sql:
                return {"refund_status": "full_refunded"}
            if "SELECT * FROM wechat_pay_orders" in self.last_sql:
                return {
                    "id": refund_row["order_id"],
                    "out_trade_no": refund_row["out_trade_no"],
                    "product_code": "subscription_trial_month",
                    "amount_total": refund_row["refund_amount_total"],
                    "refunded_amount_total": refund_row["refund_amount_total"],
                    "refund_status": "full_refunded",
                }
            return None

    class FakeConnection:
        def __init__(self):
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            self.commits += 1

    connections: list[FakeConnection] = []

    def connect(*args, **kwargs):
        conn = FakeConnection()
        connections.append(conn)
        return conn

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    psycopg_types = types.ModuleType("psycopg.types")
    json_module = types.ModuleType("psycopg.types.json")
    json_module.Jsonb = lambda value: value
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)
    monkeypatch.setitem(sys.modules, "psycopg.types", psycopg_types)
    monkeypatch.setitem(sys.modules, "psycopg.types.json", json_module)
    monkeypatch.setattr(admin_transactions, "database_mode", lambda: "postgres")
    monkeypatch.setattr(admin_transactions, "_database_url", lambda: "postgresql://test/test")
    return executed, connections


def test_next_postgres_refund_result_updates_order_once(monkeypatch):
    executed, connections = _install_fetching_fake_psycopg(
        monkeypatch,
        {
            "id": 4,
            "order_id": 147,
            "out_trade_no": "WXP_REAL_REFUND",
            "transaction_id": "420000REALNEXT",
            "out_refund_no": "WXRTEST0001",
            "refund_id": "503000000020260615",
            "status": "PROCESSING",
            "refund_amount_total": 6900,
        },
    )
    outbox_calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        admin_transactions,
        "enqueue_transactional_internal_event_outbox",
        lambda conn, request: outbox_calls.append((conn, request)) or {"outbox_id": "ieo_refund_001"},
    )

    result = admin_transactions.apply_wechat_refund_result(
        {
            "out_trade_no": "WXP_REAL_REFUND",
            "transaction_id": "420000REALNEXT",
            "out_refund_no": "WXRTEST0001",
            "refund_id": "503000000020260615",
            "refund_status": "SUCCESS",
            "amount": {"refund": 6900, "total": 6900, "currency": "CNY"},
        },
        raw_event={"event_type": "REFUND.SUCCESS", "id": "notify-refund-001"},
    )

    assert result["refund"]["status"] == "SUCCESS"
    assert result["order_refund_status"] == "full_refunded"
    assert result["service_period_refund"] == {
        "queued": True,
        "event_type": "refund.succeeded",
        "outbox_id": "ieo_refund_001",
        "real_external_call_executed": False,
    }
    assert result["updated_order_amount"] is True
    assert outbox_calls[0][0] is connections[0]
    assert outbox_calls[0][1].idempotency_key == "refund.succeeded:WXRTEST0001"
    sql_text = "\n".join(sql for sql, _params in executed)
    assert "UPDATE wechat_pay_refunds" in sql_text
    assert "UPDATE wechat_pay_orders" in sql_text
    assert any("refund_succeeded" in params for _sql, params in executed)
    assert connections[0].commits == 1


def test_next_postgres_refund_result_is_idempotent_when_already_success(monkeypatch):
    executed, _connections = _install_fetching_fake_psycopg(
        monkeypatch,
        {
            "id": 4,
            "order_id": 147,
            "out_trade_no": "WXP_REAL_REFUND",
            "transaction_id": "420000REALNEXT",
            "out_refund_no": "WXRTEST0001",
            "refund_id": "503000000020260615",
            "status": "SUCCESS",
            "refund_amount_total": 6900,
        },
    )

    outbox_calls: list[object] = []
    monkeypatch.setattr(
        admin_transactions,
        "enqueue_transactional_internal_event_outbox",
        lambda conn, request: outbox_calls.append(request) or {"outbox_id": "ieo_refund_001"},
    )

    result = admin_transactions.apply_wechat_refund_result(
        {
            "out_refund_no": "WXRTEST0001",
            "refund_id": "503000000020260615",
            "refund_status": "SUCCESS",
            "amount": {"refund": 6900, "total": 6900, "currency": "CNY"},
        }
    )

    assert result["updated_order_amount"] is False
    assert outbox_calls[0].idempotency_key == "refund.succeeded:WXRTEST0001"
    sql_text = "\n".join(sql for sql, _params in executed)
    assert "UPDATE wechat_pay_refunds" in sql_text
    assert "UPDATE wechat_pay_orders" not in sql_text


def test_next_refund_notify_route_returns_wechat_success(next_client, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_handle(body: str, headers: dict):
        calls.append((body, headers))
        return {"ok": True}

    monkeypatch.setattr(commerce_api, "handle_wechat_refund_notify", fake_handle)

    response = next_client.post("/api/h5/wechat-pay/refund/notify", content='{"event_type":"REFUND.SUCCESS"}')

    assert response.status_code == 200
    assert response.json() == {"code": "SUCCESS", "message": "成功"}
    assert calls[0][0] == '{"event_type":"REFUND.SUCCESS"}'
