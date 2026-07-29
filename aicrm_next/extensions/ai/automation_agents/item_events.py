from __future__ import annotations

from typing import Any

from aicrm_next.platform.platform_foundation.internal_events import (
    InternalEvent,
    InternalEventConsumerRegistry,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
    current_internal_event_consumer_registry,
)

from .worker import AutomationAgentWorker


ITEM_PREPARE_EVENT = "automation_agent.item.prepare"
ITEM_PREPARE_CONSUMER = "automation_agent_item_prepare_consumer"


def _text(value: Any) -> str:
    return str(value or "").strip()


def automation_agent_item_prepare_consumer(
    event: InternalEvent,
    run: InternalEventConsumerRun,
) -> InternalEventConsumerResult:
    payload = dict(event.payload_json or {})
    try:
        item_id = int(payload.get("item_id") or event.aggregate_id or 0)
    except (TypeError, ValueError):
        item_id = 0
    request_summary = {
        "event_id": event.event_id,
        "consumer_name": run.consumer_name,
        "item_id": item_id,
        "batch_id": _text(payload.get("batch_id")),
    }
    if item_id <= 0:
        return InternalEventConsumerResult(
            status="failed_terminal",
            request_summary=request_summary,
            response_summary={"item_identifier_present": False},
            error_code="automation_agent_item_id_missing",
            error_message="automation agent item prepare event is missing item_id",
        )
    result = AutomationAgentWorker().prepare_item(
        item_id,
        source_event_id=event.event_id,
        parent_execution_id=event.execution_id,
    )
    business_terminal = _text(result.get("status")) == "failed"
    succeeded = bool(result.get("ok")) or business_terminal
    response_summary = {
        key: result.get(key)
        for key in (
            "ok",
            "deduplicated",
            "item_id",
            "status",
            "external_effect_job_id",
            "lane",
            "error",
        )
        if key in result
    }
    return InternalEventConsumerResult(
        status="succeeded" if succeeded else "failed_retryable",
        request_summary=request_summary,
        response_summary=response_summary,
        result_summary=response_summary,
        error_code="" if succeeded else _text(result.get("error")) or "automation_agent_item_prepare_failed",
        error_message="" if succeeded else _text(result.get("detail") or result.get("error")),
        retry_after_seconds=None if succeeded else 30,
    )


def register_automation_agent_event_consumers(
    registry: InternalEventConsumerRegistry | None = None,
) -> None:
    registry = registry or current_internal_event_consumer_registry()
    registry.register(
        ITEM_PREPARE_EVENT,
        ITEM_PREPARE_CONSUMER,
        automation_agent_item_prepare_consumer,
        consumer_type="external_effect_planner",
        max_attempts=10,
    )


__all__ = [
    "ITEM_PREPARE_CONSUMER",
    "ITEM_PREPARE_EVENT",
    "automation_agent_item_prepare_consumer",
    "register_automation_agent_event_consumers",
]
