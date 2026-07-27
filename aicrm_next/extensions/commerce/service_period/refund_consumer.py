from __future__ import annotations

from typing import Any

from aicrm_next.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerResult, InternalEventConsumerRun

from .application import ApplyServicePeriodRefundCommand


def _text(value: Any) -> str:
    return str(value or "").strip()


def _payload(event: InternalEvent, key: str) -> dict[str, Any]:
    value = dict(event.payload_json or {}).get(key)
    return dict(value) if isinstance(value, dict) else {}


def service_period_refund_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    refund = _payload(event, "refund")
    order = _payload(event, "order")
    out_trade_no = _text(order.get("out_trade_no") or event.correlation_id)
    if not out_trade_no:
        return InternalEventConsumerResult(
            status="failed_terminal",
            request_summary={"event_id": event.event_id, "aggregate_id": event.aggregate_id},
            response_summary={"out_trade_no_present": False},
            error_code="out_trade_no_missing",
            error_message="refund.succeeded event is missing out_trade_no",
        )
    try:
        result = ApplyServicePeriodRefundCommand()(out_trade_no=out_trade_no, refund=refund)
    except Exception as exc:
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"entitlement_refund_applied": False},
            error_code="service_period_refund_failed",
            error_message=str(exc)[:500],
            retry_after_seconds=300,
        )
    reason = _text(result.get("reason"))
    if reason == "not_service_period_order":
        return InternalEventConsumerResult(
            status="skipped",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"skipped": True, "reason": reason},
            result_summary={"reason": reason},
        )
    if reason in {"entitlement_not_found", "service_period_product_not_found"}:
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"skipped": True, "reason": reason},
            error_code=reason,
            error_message="refunded service-period order is not yet consistent with its entitlement projection",
            retry_after_seconds=300,
        )
    if not result.get("ok", False):
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
            response_summary={"skipped": bool(result.get("skipped")), "reason": reason},
            error_code=reason or "service_period_refund_failed",
            error_message="service-period refund projection failed",
            retry_after_seconds=300,
        )
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary={"event_id": event.event_id, "out_trade_no": out_trade_no},
        response_summary={"idempotent": bool(result.get("idempotent")), "reason": reason},
        result_summary={
            "event_type": result.get("event_type") or "refunded",
            "out_trade_no": out_trade_no,
            "entitlement_id": (result.get("entitlement") or {}).get("id", ""),
        },
    )


__all__ = ["service_period_refund_consumer"]
