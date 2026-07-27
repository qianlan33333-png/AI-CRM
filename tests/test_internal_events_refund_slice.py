from __future__ import annotations

from aicrm_next.internal_event_composition import register_refund_succeeded_consumers
from aicrm_next.platform.platform_foundation.internal_events.consumer_registry import InternalEventConsumerRegistry
from aicrm_next.platform.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.platform.platform_foundation.internal_events.refund import (
    REFUND_SUCCEEDED_CONSUMER,
    REFUND_SUCCEEDED_EVENT_TYPE,
    build_refund_succeeded_event_request,
)
from aicrm_next.extensions.commerce.service_period import refund_consumer


def _request():
    return build_refund_succeeded_event_request(
        refund={
            "out_refund_no": "WXR_R08_001",
            "refund_id": "wx_refund_r08_001",
            "status": "SUCCESS",
            "amount_total": 6900,
            "order_refund_status": "full_refunded",
            "mobile": "13800001234",
        },
        order={
            "id": 81,
            "out_trade_no": "WXP_R08_001",
            "product_code": "subscription_trial_month",
            "refund_status": "full_refunded",
            "refunded_amount_total": 6900,
            "amount_total": 6900,
            "unionid": "union_should_not_be_copied",
        },
    )


def _event() -> InternalEvent:
    request = _request()
    assert request is not None
    return InternalEvent(
        event_id="iev_refund_r08_001",
        event_type=request.event_type,
        aggregate_type=request.aggregate_type,
        aggregate_id=request.aggregate_id,
        correlation_id=request.correlation_id,
        payload_json=request.payload,
        payload_summary_json=request.payload_summary,
    )


def _run() -> InternalEventConsumerRun:
    return InternalEventConsumerRun(
        event_id="iev_refund_r08_001",
        consumer_name=REFUND_SUCCEEDED_CONSUMER,
    )


class _Command:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or {}
        self.error = error

    def __call__(self, **kwargs):
        if self.error:
            raise self.error
        return dict(self.result)


def test_refund_succeeded_request_is_idempotent_minimal_and_pii_free() -> None:
    request = _request()

    assert request is not None
    assert request.event_type == REFUND_SUCCEEDED_EVENT_TYPE
    assert request.idempotency_key == "refund.succeeded:WXR_R08_001"
    assert request.aggregate_type == "wechat_pay_refund"
    assert request.aggregate_id == "WXR_R08_001"
    assert request.correlation_id == "WXP_R08_001"
    serialized = str({"payload": request.payload, "summary": request.payload_summary})
    assert "13800001234" not in serialized
    assert "union_should_not_be_copied" not in serialized


def test_refund_succeeded_consumer_registration_has_one_owner() -> None:
    registry = InternalEventConsumerRegistry()
    register_refund_succeeded_consumers(registry)

    consumers = registry.list_for_event_type(REFUND_SUCCEEDED_EVENT_TYPE)
    assert [consumer.consumer_name for consumer in consumers] == [REFUND_SUCCEEDED_CONSUMER]


def test_refund_consumer_maps_skip_retry_and_idempotent_success(monkeypatch) -> None:
    monkeypatch.setattr(
        refund_consumer,
        "ApplyServicePeriodRefundCommand",
        lambda: _Command({"ok": True, "skipped": True, "reason": "not_service_period_order"}),
    )
    skipped = refund_consumer.service_period_refund_consumer(_event(), _run())

    monkeypatch.setattr(
        refund_consumer,
        "ApplyServicePeriodRefundCommand",
        lambda: _Command({"ok": True, "skipped": True, "reason": "entitlement_not_found"}),
    )
    retryable = refund_consumer.service_period_refund_consumer(_event(), _run())

    monkeypatch.setattr(
        refund_consumer,
        "ApplyServicePeriodRefundCommand",
        lambda: _Command(
            {
                "ok": True,
                "idempotent": True,
                "event_type": "refunded",
                "entitlement": {"id": "ent_81"},
            }
        ),
    )
    succeeded = refund_consumer.service_period_refund_consumer(_event(), _run())

    assert skipped.status == "skipped"
    assert skipped.result_summary["reason"] == "not_service_period_order"
    assert retryable.status == "failed_retryable"
    assert retryable.error_code == "entitlement_not_found"
    assert retryable.retry_after_seconds == 300
    assert succeeded.status == "succeeded"
    assert succeeded.response_summary["idempotent"] is True
    assert succeeded.result_summary["entitlement_id"] == "ent_81"


def test_refund_consumer_turns_projection_exception_into_retryable(monkeypatch) -> None:
    monkeypatch.setattr(
        refund_consumer,
        "ApplyServicePeriodRefundCommand",
        lambda: _Command(error=RuntimeError("injected entitlement persistence fault")),
    )

    result = refund_consumer.service_period_refund_consumer(_event(), _run())

    assert result.status == "failed_retryable"
    assert result.error_code == "service_period_refund_failed"
    assert result.retry_after_seconds == 300
