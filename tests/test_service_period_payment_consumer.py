from __future__ import annotations

from aicrm_next.platform.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.extensions.commerce.service_period import payment_consumer


def test_missing_unionid_entitlement_consumer_is_retryable_and_never_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        payment_consumer,
        "GrantOrRenewEntitlementCommand",
        lambda: lambda **_kwargs: {"ok": False, "skipped": True, "reason": "missing_unionid"},
    )
    event = InternalEvent(
        event_type="payment.succeeded",
        event_id="event-r03-missing-unionid",
        aggregate_id="order-r03-missing-unionid",
        payload_json={
            "order": {
                "out_trade_no": "order-r03-missing-unionid",
                "status": "paid",
                "trade_state": "SUCCESS",
            }
        },
    )

    result = payment_consumer.service_period_entitlement_consumer(
        event,
        InternalEventConsumerRun(consumer_name="service_period_entitlement_consumer"),
    )

    assert result.status == "failed_retryable"
    assert result.error_code == "missing_unionid"
    assert result.response_summary["reason"] == "missing_unionid"
