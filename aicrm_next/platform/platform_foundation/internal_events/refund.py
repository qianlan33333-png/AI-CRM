from __future__ import annotations

from typing import Any

from aicrm_next.platform.platform_foundation.command_bus import CommandContext

from .consumer_registry import (
    InternalEventConsumerHandler,
    InternalEventConsumerRegistry,
    current_internal_event_consumer_registry,
)
from .models import InternalEventCreateRequest

REFUND_SUCCEEDED_EVENT_TYPE = "refund.succeeded"
REFUND_SUCCEEDED_CONSUMER = "service_period_refund_consumer"
REFUND_SUCCEEDED_PRODUCTION_EVENT_CONSUMERS = (f"{REFUND_SUCCEEDED_EVENT_TYPE}:{REFUND_SUCCEEDED_CONSUMER}",)


def _text(value: Any) -> str:
    return str(value or "").strip()


def build_refund_succeeded_event_request(
    *,
    refund: dict[str, Any],
    order: dict[str, Any],
    source_route: str = "",
) -> InternalEventCreateRequest | None:
    out_refund_no = _text(refund.get("out_refund_no"))
    out_trade_no = _text(order.get("out_trade_no") or refund.get("out_trade_no"))
    if not out_refund_no or not out_trade_no:
        return None
    order_id = _text(order.get("id") or out_trade_no)
    refund_payload = {
        "out_refund_no": out_refund_no,
        "refund_id": _text(refund.get("refund_id")),
        "status": _text(refund.get("status")) or "SUCCESS",
        "amount_total": int(refund.get("amount_total") or refund.get("refund_amount_total") or 0),
        "order_refund_status": _text(refund.get("order_refund_status")),
    }
    order_payload = {
        "id": order.get("id"),
        "out_trade_no": out_trade_no,
        "product_code": _text(order.get("product_code")),
        "refund_status": _text(order.get("refund_status") or refund_payload["order_refund_status"]),
        "refunded_amount_total": int(order.get("refunded_amount_total") or 0),
        "amount_total": int(order.get("amount_total") or 0),
    }
    return InternalEventCreateRequest(
        event_type=REFUND_SUCCEEDED_EVENT_TYPE,
        event_version=1,
        aggregate_type="wechat_pay_refund",
        aggregate_id=out_refund_no,
        subject_type="commerce_order",
        subject_id=order_id,
        idempotency_key=f"refund.succeeded:{out_refund_no}",
        source_module="commerce.admin_transactions",
        source_command_id=out_refund_no,
        correlation_id=out_trade_no,
        context=CommandContext(
            actor_id="wechat_pay_refund_notify",
            actor_type="system",
            trace_id=out_trade_no,
            request_id=_text(refund.get("refund_id")) or out_refund_no,
            source_route=source_route or "/api/h5/wechat-pay/refund/notify",
        ),
        payload={"refund": refund_payload, "order": order_payload},
        payload_summary={
            "out_refund_no": out_refund_no,
            "out_trade_no": out_trade_no,
            "order_id": order.get("id"),
            "order_refund_status": refund_payload["order_refund_status"],
            "refund_amount_total": refund_payload["amount_total"],
            "provider_status": refund_payload["status"],
        },
    )


def register_refund_succeeded_consumers(
    registry: InternalEventConsumerRegistry | None = None,
    *,
    service_period_consumer: InternalEventConsumerHandler | None = None,
) -> None:
    registry = registry or current_internal_event_consumer_registry()
    if service_period_consumer is not None:
        registry.register(
            REFUND_SUCCEEDED_EVENT_TYPE,
            REFUND_SUCCEEDED_CONSUMER,
            service_period_consumer,
            consumer_type="projection",
        )


__all__ = [
    "REFUND_SUCCEEDED_CONSUMER",
    "REFUND_SUCCEEDED_EVENT_TYPE",
    "REFUND_SUCCEEDED_PRODUCTION_EVENT_CONSUMERS",
    "build_refund_succeeded_event_request",
    "register_refund_succeeded_consumers",
]
