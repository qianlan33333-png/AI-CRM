from __future__ import annotations

from pathlib import Path

from aicrm_next.crm.identity_contact.dto import IdentityResolution, IdentityResolveResult
from aicrm_next.crm.identity_contact import payment_projection
from aicrm_next.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.platform_foundation.internal_events.payment import order_projection_consumer
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay

ROOT = Path(__file__).resolve().parents[1]


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
            external_userid="wm_order_001",
            mobile="15812345678",
            unionid=unionid,
            binding_status="bound",
        ),
        candidate_count=1,
    )


def test_project_order_mobile_to_identity_uses_order_metadata(monkeypatch) -> None:
    monkeypatch.setattr(payment_projection, "resolve_identity_with_dbapi", lambda *_args, **_kwargs: _resolved("union_order_001"))
    conn = _ProjectionConn({"unionid": "union_order_001", "mobile": "15812345678"})

    result = h5_wechat_pay._project_order_mobile_to_identity(
        conn,
        {
            "out_trade_no": "WXP_MOBILE_PROJECT",
            "unionid": "union_order_001",
            "payer_name_snapshot": "付款人",
            "metadata_json": {
                "payer_identity": {
                    "mobile": "158 1234 5678",
                    "external_userid": "wm_order_001",
                    "owner_userid": "HuangYouCan",
                }
            },
        },
        source_route="/api/h5/wechat-pay/notify",
    )

    assert result == {"ok": True, "projected": True, "unionid": "union_order_001", "mobile": "15812345678"}
    call = conn.calls[0]
    assert "INSERT INTO crm_user_identity" in call["query"]
    assert "ON CONFLICT (unionid) DO UPDATE SET" in call["query"]
    assert "WHERE COALESCE(crm_user_identity.mobile, '') = ''" in call["query"]
    assert call["params"][0:4] == ("union_order_001", "wm_order_001", "wm_order_001", "wm_order_001")
    assert call["params"][7:11] == ("15812345678", "15812345678", "wechat_pay_order", "付款人")
    assert call["params"][12] == "HuangYouCan"


def test_project_order_mobile_to_identity_skips_invalid_or_missing_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        payment_projection,
        "resolve_identity_with_dbapi",
        lambda *_args, **_kwargs: IdentityResolveResult(status="not_found", reason="identity_not_found"),
    )
    conn = _ProjectionConn({"unionid": "union_order_001"})

    missing_unionid = h5_wechat_pay._project_order_mobile_to_identity(
        conn,
        {"metadata_json": {"payer_identity": {"mobile": "15812345678"}}},
        source_route="/api/h5/wechat-pay/notify",
    )
    invalid_mobile = h5_wechat_pay._project_order_mobile_to_identity(
        conn,
        {"unionid": "union_order_001", "metadata_json": {"payer_identity": {"mobile": "12345"}}},
        source_route="/api/h5/wechat-pay/notify",
    )

    assert missing_unionid["reason"] == "missing_unionid"
    assert invalid_mobile["reason"] == "invalid_mobile"
    assert not any("INSERT INTO crm_user_identity" in call["query"] for call in conn.calls)


def test_project_order_mobile_to_identity_creates_canonical_row_when_identity_arrives_late(monkeypatch) -> None:
    monkeypatch.setattr(
        payment_projection,
        "resolve_identity_with_dbapi",
        lambda *_args, **_kwargs: IdentityResolveResult(status="not_found", reason="identity_not_found"),
    )
    conn = _ProjectionConn({"unionid": "union_late_001", "mobile": "15812345678"})

    result = h5_wechat_pay._project_order_mobile_to_identity(
        conn,
        {
            "out_trade_no": "WXP_IDENTITY_LATE",
            "unionid": "union_late_001",
            "metadata_json": {"payer_identity": {"mobile": "15812345678", "openid": "op_late_001"}},
        },
        source_route="/api/h5/wechat-pay/notify",
    )

    assert result == {"ok": True, "projected": True, "unionid": "union_late_001", "mobile": "15812345678"}
    assert len(conn.calls) == 1
    assert "INSERT INTO crm_user_identity" in conn.calls[0]["query"]
    assert "ON CONFLICT (unionid) DO UPDATE SET" in conn.calls[0]["query"]


def test_project_order_mobile_to_identity_does_not_overwrite_conflicting_mobile(monkeypatch) -> None:
    monkeypatch.setattr(
        payment_projection,
        "resolve_identity_with_dbapi",
        lambda _conn, query, **_kwargs: (
            IdentityResolveResult(status="not_found", reason="identity_not_found")
            if query.mobile
            else _resolved("union_order_001")
        ),
    )
    conn = _ProjectionConn(None)

    result = h5_wechat_pay._project_order_mobile_to_identity(
        conn,
        {
            "unionid": "union_order_001",
            "metadata_json": {"payer_identity": {"mobile": "15812345678"}},
        },
        source_route="/api/h5/wechat-pay/notify",
    )

    assert result == {
        "ok": False,
        "projected": False,
        "reason": "mobile_alias_conflict",
        "unionid": "union_order_001",
    }


def test_apply_transaction_runs_mobile_projection_before_canonical_internal_event(monkeypatch) -> None:
    calls: list[str] = []

    class Conn:
        def execute(self, query, params):
            if query.startswith("SELECT * FROM wechat_pay_orders"):
                return _Cursor({"status": "paying", "amount_total": 990, "currency": "CNY"})
            if query.strip().startswith("UPDATE wechat_pay_orders"):
                return _Cursor(
                    {
                        "id": 1,
                        "out_trade_no": "WXP_APPLY_MOBILE",
                        "status": "paid",
                        "trade_state": "SUCCESS",
                        "unionid": "union_apply_001",
                        "metadata_json": {"payer_identity": {"mobile": "15812345678"}},
                    }
                )
            raise AssertionError(query)

    monkeypatch.setattr(
        h5_wechat_pay,
        "_safe_project_order_mobile_to_identity",
        lambda conn, order, *, source_route: calls.append("project") or {"ok": True, "projected": True},
    )
    monkeypatch.setattr(
        h5_wechat_pay,
        "_enqueue_payment_succeeded_internal_event_outbox",
        lambda conn, **kwargs: calls.append("event_outbox"),
    )

    order = h5_wechat_pay._apply_transaction(
        Conn(),
        {
            "out_trade_no": "WXP_APPLY_MOBILE",
            "trade_state": "SUCCESS",
            "amount": {"total": 990},
            "success_time": "2026-07-07T07:33:14Z",
        },
    )

    assert order["status"] == "paid"
    assert calls == ["project", "event_outbox"]


def test_payment_order_projection_consumer_retries_transient_identity_write_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "aicrm_next.platform_foundation.internal_events.payment._read_order_from_db",
        lambda _event: {},
    )
    event = InternalEvent(
        event_type="payment.succeeded",
        aggregate_id="WXP_RETRY_MOBILE",
        payload_json={
            "order": {
                "out_trade_no": "WXP_RETRY_MOBILE",
                "status": "paid",
                "trade_state": "SUCCESS",
                "unionid": "union_retry_001",
                "metadata_json": {"payer_identity": {"mobile": "15812345678"}},
            }
        },
    )

    def fail_projection(**_kwargs):
        raise RuntimeError("temporary database failure")

    result = order_projection_consumer(
        event,
        InternalEventConsumerRun(consumer_name="order_projection_consumer"),
        identity_projector=fail_projection,
    )

    assert result.status == "failed_retryable"
    assert result.error_code == "payment_identity_projection_failed"
    assert result.retry_after_seconds == 60
    assert "15812345678" not in str(result.response_summary)


def test_payment_order_projection_consumer_records_successful_mobile_retry(monkeypatch) -> None:
    monkeypatch.setattr(
        "aicrm_next.platform_foundation.internal_events.payment._read_order_from_db",
        lambda _event: {},
    )
    event = InternalEvent(
        event_type="payment.succeeded",
        aggregate_id="WXP_RETRY_MOBILE_OK",
        payload_json={
            "order": {
                "out_trade_no": "WXP_RETRY_MOBILE_OK",
                "status": "paid",
                "trade_state": "SUCCESS",
                "unionid": "union_retry_ok_001",
            }
        },
    )

    result = order_projection_consumer(
        event,
        InternalEventConsumerRun(consumer_name="order_projection_consumer"),
        identity_projector=lambda **_kwargs: {
            "ok": True,
            "projected": True,
            "unionid": "union_retry_ok_001",
            "mobile": "15812345678",
        },
    )

    assert result.status == "succeeded"
    assert result.response_summary["mobile_projection_attempted"] is True
    assert result.response_summary["mobile_projected"] is True
    assert "15812345678" not in str(result.response_summary)


def test_order_read_models_fallback_to_metadata_mobile_for_historical_orders() -> None:
    sidebar_source = (ROOT / "aicrm_next/crm/customer_read_model/sidebar_v2.py").read_text(encoding="utf-8")
    transactions_source = (ROOT / "aicrm_next/extensions/commerce/commerce/admin_transactions.py").read_text(encoding="utf-8")
    detail_source = (ROOT / "aicrm_next/extensions/commerce/commerce/admin_transaction_detail.py").read_text(encoding="utf-8")

    assert "o.metadata_json #>> '{payer_identity,mobile}'" in sidebar_source
    assert "o.metadata_json #>> '{buyer_identity,mobile}'" in sidebar_source
    assert "NULLIF((SELECT identity.mobile" in transactions_source
    assert "metadata_json #>> '{{payer_identity,mobile}}'" in transactions_source
    assert "metadata_json #>> '{{buyer_identity,mobile}}'" in transactions_source
    assert "NULLIF((SELECT identity.mobile" in detail_source
    assert "o.metadata_json #>> '{{payer_identity,mobile}}'" in detail_source
    assert "o.metadata_json #>> '{{buyer_identity,mobile}}'" in detail_source
