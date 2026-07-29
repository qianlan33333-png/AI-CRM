from __future__ import annotations

from aicrm_next.extensions.commerce.commerce import wechat_shop_service
from aicrm_next.crm.identity_contact.dto import IdentityResolution, IdentityResolveResult
from aicrm_next.crm.identity_contact import payment_projection


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _ProjectionConn:
    def __init__(self, row):
        self.row = row
        self.calls: list[dict] = []

    def execute(self, query, params):
        self.calls.append({"query": query, "params": params})
        return _Cursor(self.row)


def _resolved(unionid: str) -> IdentityResolveResult:
    return IdentityResolveResult(
        status="resolved",
        identity=IdentityResolution(
            person_id=None,
            external_userid="",
            mobile="15912345678",
            unionid=unionid,
            binding_status="bound",
        ),
        candidate_count=1,
    )


def test_project_paid_wechat_shop_mobile_into_canonical_identity(monkeypatch) -> None:
    monkeypatch.setattr(payment_projection, "resolve_identity_with_dbapi", lambda *_args, **_kwargs: _resolved("union_shop_001"))
    conn = _ProjectionConn({"unionid": "union_shop_001", "mobile": "15912345678"})

    result = payment_projection.project_wechat_shop_order_mobile(
        conn,
        {
            "order_id": "3737749464399107840",
            "paid_at": "2026-07-14T01:34:29Z",
            "unionid": "union_shop_001",
            "openid": "openid_shop_001",
            "buyer_mobile": "159 1234 5678",
        },
        source_route="wechat_shop_order_sync",
    )

    assert result == {"ok": True, "projected": True, "unionid": "union_shop_001", "mobile": "15912345678"}
    call = conn.calls[0]
    assert "INSERT INTO crm_user_identity" in call["query"]
    assert call["params"][0] == "union_shop_001"
    assert call["params"][4:7] == ("openid_shop_001", "openid_shop_001", "openid_shop_001")
    assert call["params"][7:10] == ("15912345678", "15912345678", "wechat_shop_order")
    assert "wechat_shop_mobile_projection" in call["params"][11]
    assert "3737749464399107840" in call["params"][11]


def test_wechat_shop_projection_skips_unpaid_order(monkeypatch) -> None:
    monkeypatch.setattr(
        payment_projection,
        "resolve_identity_with_dbapi",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resolver should not run")),
    )

    result = payment_projection.project_wechat_shop_order_mobile(
        _ProjectionConn(None),
        {"order_id": "unpaid", "unionid": "union_unpaid", "buyer_mobile": "15912345678"},
        source_route="wechat_shop_order_sync",
    )

    assert result == {"ok": True, "projected": False, "reason": "order_not_paid"}


def test_wechat_shop_upsert_projects_in_same_postgres_transaction(monkeypatch) -> None:
    calls: list[tuple] = []

    class _Context:
        def __init__(self):
            self.conn = _ProjectionConn({"order_id": "shop-order-atomic"})

        def __enter__(self):
            return self.conn

        def __exit__(self, exc_type, exc, traceback):
            calls.append(("exit", exc_type))

    monkeypatch.setattr(wechat_shop_service, "database_mode", lambda: "postgres")
    monkeypatch.setattr(wechat_shop_service, "_connect", _Context)
    monkeypatch.setattr(wechat_shop_service, "_jsonb", lambda value: value)
    monkeypatch.setattr(wechat_shop_service, "_latest_event_type", lambda _event_id: "")
    monkeypatch.setattr(
        wechat_shop_service,
        "project_wechat_shop_order_mobile",
        lambda conn, order, *, source_route: calls.append(("project", conn, order["order_id"], source_route)),
    )

    saved = wechat_shop_service._upsert_order(
        {
            "order_id": "shop-order-atomic",
            "paid_at": "2026-07-14T01:34:29Z",
            "raw_order_json": {},
        }
    )

    assert saved == {"order_id": "shop-order-atomic"}
    assert calls[0][0] == "project"
    assert calls[0][2:] == ("shop-order-atomic", "wechat_shop_order_sync")
    assert calls[1] == ("exit", None)
