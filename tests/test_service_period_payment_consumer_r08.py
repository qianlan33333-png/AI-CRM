from __future__ import annotations

from aicrm_next.platform.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.extensions.commerce.service_period import payment_consumer


def _event() -> InternalEvent:
    return InternalEvent(
        event_id="iev_payment_r08_001",
        event_type="payment.succeeded",
        aggregate_id="81",
        payload_json={
            "order": {
                "id": 81,
                "out_trade_no": "WXP_R08_PAYMENT_001",
                "product_code": "sp_r08",
                "status": "paid",
                "trade_state": "SUCCESS",
                "unionid": "",
            },
            "transaction": {"out_trade_no": "WXP_R08_PAYMENT_001"},
        },
    )


def _run() -> InternalEventConsumerRun:
    return InternalEventConsumerRun(
        event_id="iev_payment_r08_001",
        consumer_name="service_period_entitlement_consumer",
    )


def test_payment_consumer_reloads_authoritative_order_after_identity_backfill(monkeypatch) -> None:
    captured: list[dict] = []

    class Command:
        def __call__(self, *, order, transaction):
            captured.append({"order": dict(order), "transaction": dict(transaction)})
            return {"ok": True, "event_type": "activated", "entitlement": {"id": "ent_81"}}

    monkeypatch.setattr(
        payment_consumer,
        "read_wechat_pay_order_for_payment_event",
        lambda **kwargs: {
            "id": 81,
            "out_trade_no": "WXP_R08_PAYMENT_001",
            "product_code": "sp_r08",
            "status": "paid",
            "trade_state": "SUCCESS",
            "unionid": "union_after_backfill",
        },
    )
    monkeypatch.setattr(payment_consumer, "GrantOrRenewEntitlementCommand", Command)

    result = payment_consumer.service_period_entitlement_consumer(_event(), _run())

    assert result.status == "succeeded"
    assert captured[0]["order"]["unionid"] == "union_after_backfill"
    assert result.result_summary["entitlement_id"] == "ent_81"


def test_payment_consumer_keeps_database_and_projection_faults_retryable(monkeypatch) -> None:
    def fail_read(**kwargs):
        raise RuntimeError("injected authoritative read fault")

    monkeypatch.setattr(payment_consumer, "read_wechat_pay_order_for_payment_event", fail_read)
    read_failed = payment_consumer.service_period_entitlement_consumer(_event(), _run())

    class FailedCommand:
        def __call__(self, **kwargs):
            return {"ok": False, "reason": "entitlement_write_failed"}

    monkeypatch.setattr(
        payment_consumer,
        "read_wechat_pay_order_for_payment_event",
        lambda **kwargs: dict(_event().payload_json["order"], unionid="union_ready"),
    )
    monkeypatch.setattr(payment_consumer, "GrantOrRenewEntitlementCommand", FailedCommand)
    projection_failed = payment_consumer.service_period_entitlement_consumer(_event(), _run())

    assert read_failed.status == "failed_retryable"
    assert read_failed.error_code == "order_read_failed"
    assert read_failed.retry_after_seconds == 300
    assert projection_failed.status == "failed_retryable"
    assert projection_failed.error_code == "entitlement_write_failed"
