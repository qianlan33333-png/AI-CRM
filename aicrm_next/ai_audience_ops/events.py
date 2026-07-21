from __future__ import annotations

from typing import Any

from aicrm_next.platform_foundation.internal_events import (
    InternalEvent,
    InternalEventConsumerRegistry,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
    PAYMENT_SUCCEEDED_EVENT_TYPES,
    QUESTIONNAIRE_SUBMITTED_EVENT_TYPE,
    current_internal_event_consumer_registry,
)

from .event_types import (
    DAILY_REFRESH_CONSUMER,
    DAILY_TICK_EVENT,
    INCREMENTAL_REFRESH_CONSUMER,
    INCREMENTAL_TICK_EVENT,
    INBOUND_ACTION_CONSUMER,
    INBOUND_RECEIVED_EVENT,
    OUTBOUND_EFFECT_CONSUMER,
    REFRESH_INTENT_CONSUMER,
    REFRESH_REQUESTED_EVENT,
    RUN_REFRESHED_EVENT,
    SOURCE_CHANGED_EVENT,
    SOURCE_POKE_CONSUMER,
)
from .outbound_service import AudienceOutboundService
from .refresh_intents import AudienceRefreshIntentService
from .repository import _text
from .webhook_service import AudienceInboundWebhookService


def incremental_refresh_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    result = AudienceRefreshIntentService().request_due_refreshes(
        "incremental",
        bucket=_text((event.payload_json or {}).get("bucket")) or event.event_id,
        actor_id=run.consumer_name,
    )
    return InternalEventConsumerResult(
        status="succeeded" if result.get("ok") else "failed_retryable",
        request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
        response_summary=_summary(result),
        result_summary=_summary(result),
        error_code="" if result.get("ok") else _text(result.get("error")) or "ai_audience_incremental_refresh_failed",
        error_message="" if result.get("ok") else _text(result.get("error")),
    )


def daily_refresh_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    result = AudienceRefreshIntentService().request_due_refreshes(
        "daily",
        bucket=_text((event.payload_json or {}).get("bucket")) or event.event_id,
        actor_id=run.consumer_name,
    )
    return InternalEventConsumerResult(
        status="succeeded" if result.get("ok") else "failed_retryable",
        request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
        response_summary=_summary(result),
        result_summary=_summary(result),
        error_code="" if result.get("ok") else _text(result.get("error")) or "ai_audience_daily_refresh_failed",
        error_message="" if result.get("ok") else _text(result.get("error")),
    )


def source_poke_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    payload = dict(event.payload_json or {})
    source_type, source_key = _source_from_event(event, payload)
    result = AudienceRefreshIntentService().request_source_change(
        {
            "source_type": source_type,
            "source_key": source_key,
        },
        source_event_key=_text(payload.get("source_event_key")) or event.event_id,
        execution_id=event.execution_id,
        parent_execution_id=event.parent_execution_id,
    )
    updated_count = int(result.get("updated_package_count") or 0)
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
        response_summary={"updated_package_count": updated_count},
        result_summary={"updated_package_count": updated_count},
    )


def refresh_intent_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    payload = dict(event.payload_json or {})
    package_id = int(payload.get("package_id") or event.aggregate_id or 0)
    generation = int(payload.get("generation") or 0)
    result = AudienceRefreshIntentService().process_requested(
        package_id=package_id,
        signal_generation=generation,
        owner_consumer_run_id=int(run.id or 0),
        owner_lease_token=_text(run.lease_token),
    )
    ok = bool(result.get("ok"))
    skipped = not result.get("claimed") and result.get("reason") == "already_completed"
    status = "succeeded" if ok or skipped else "failed_retryable"
    return InternalEventConsumerResult(
        status=status,
        request_summary={
            "event_id": event.event_id,
            "consumer_name": run.consumer_name,
            "package_id": package_id,
            "signal_generation": generation,
        },
        response_summary=_summary(result),
        result_summary=_summary(result),
        error_code="" if status == "succeeded" else _text(result.get("error")) or _text(result.get("reason")) or "ai_audience_refresh_intent_failed",
        error_message="" if status == "succeeded" else _text(result.get("error")) or _text(result.get("reason")),
        retry_after_seconds=None if status == "succeeded" else 60,
    )


def inbound_action_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    payload = dict(event.payload_json or {})
    inbound_event_id = int(payload.get("inbound_event_id") or event.aggregate_id or 0)
    result = AudienceInboundWebhookService().process_record(
        inbound_event_id,
        parent_execution_id=event.execution_id,
    )
    ok = bool(result.get("ok"))
    return InternalEventConsumerResult(
        status="succeeded" if ok else "failed_retryable",
        request_summary={
            "event_id": event.event_id,
            "consumer_name": run.consumer_name,
            "inbound_event_id": inbound_event_id,
        },
        response_summary=_summary(result),
        result_summary=_summary(result),
        error_code="" if ok else _text(result.get("error")) or "ai_audience_inbound_action_failed",
        error_message="" if ok else _text(result.get("error")),
    )


def outbound_effect_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    payload = dict(event.payload_json or {})
    run_id = int(payload.get("run_id") or event.aggregate_id or 0) if event.event_type == RUN_REFRESHED_EVENT else 0
    member_event_id = int(payload.get("member_event_id") or event.aggregate_id or 0)
    result = (
        AudienceOutboundService().plan_for_refresh_run(
            run_id,
            parent_execution_id=event.execution_id,
            source_event_id=event.event_id,
        )
        if run_id > 0
        else AudienceOutboundService().plan_for_member_event(
            member_event_id,
            parent_execution_id=event.execution_id,
            source_event_id=event.event_id,
        )
    )
    ok = bool(result.get("ok"))
    return InternalEventConsumerResult(
        status="succeeded" if ok else "failed_retryable",
        request_summary={
            "event_id": event.event_id,
            "consumer_name": run.consumer_name,
            "run_id": run_id,
            "member_event_id": member_event_id if run_id <= 0 else 0,
        },
        response_summary=_summary(result),
        result_summary=_summary(result),
        error_code="" if ok else _text(result.get("error")) or "ai_audience_outbound_plan_failed",
        error_message="" if ok else _text(result.get("error")),
    )


def register_ai_audience_event_consumers(registry: InternalEventConsumerRegistry | None = None) -> None:
    registry = registry or current_internal_event_consumer_registry()
    registry.register(INCREMENTAL_TICK_EVENT, INCREMENTAL_REFRESH_CONSUMER, incremental_refresh_consumer, consumer_type="orchestration")
    registry.register(DAILY_TICK_EVENT, DAILY_REFRESH_CONSUMER, daily_refresh_consumer, consumer_type="orchestration")
    registry.register(
        REFRESH_REQUESTED_EVENT,
        REFRESH_INTENT_CONSUMER,
        refresh_intent_consumer,
        consumer_type="orchestration",
        max_attempts=10,
    )
    registry.register(
        INBOUND_RECEIVED_EVENT,
        INBOUND_ACTION_CONSUMER,
        inbound_action_consumer,
        consumer_type="external_effect_planner",
    )
    registry.register(SOURCE_CHANGED_EVENT, SOURCE_POKE_CONSUMER, source_poke_consumer, consumer_type="projection")
    registry.register("channel_entry.entered", SOURCE_POKE_CONSUMER, source_poke_consumer, consumer_type="projection")
    registry.register(QUESTIONNAIRE_SUBMITTED_EVENT_TYPE, SOURCE_POKE_CONSUMER, source_poke_consumer, consumer_type="projection")
    registry.register("external_form.submitted", SOURCE_POKE_CONSUMER, source_poke_consumer, consumer_type="projection")
    for payment_event_type in PAYMENT_SUCCEEDED_EVENT_TYPES:
        registry.register(payment_event_type, SOURCE_POKE_CONSUMER, source_poke_consumer, consumer_type="projection")
    registry.register(RUN_REFRESHED_EVENT, OUTBOUND_EFFECT_CONSUMER, outbound_effect_consumer, consumer_type="external_effect_planner")


def _limit_from_event(event: InternalEvent, *, default: int) -> int:
    payload = dict(event.payload_json or {})
    try:
        return max(1, min(int(payload.get("limit") or default), 200))
    except (TypeError, ValueError):
        return default


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key in (
        "ok",
        "refresh_kind",
        "candidate_count",
        "processed_count",
        "succeeded_count",
        "failed_count",
        "member_event_count",
        "planned_count",
        "late_identity_planned_count",
        "run_id",
        "entered_count",
        "updated_count",
        "exited_count",
        "error",
        "real_external_call_executed",
        "claimed",
        "generation",
        "intent_count",
        "deduplicated_count",
        "matched_package_count",
        "updated_package_count",
        "reason",
        "inbound_event_id",
        "external_effect_job_id",
        "deduplicated",
    ):
        if key in payload:
            result[key] = payload.get(key)
    return result


def _source_from_event(event: InternalEvent, payload: dict[str, Any]) -> tuple[str, str]:
    source_type = _text(payload.get("source_type"))
    source_key = _text(payload.get("source_key"))
    if source_type:
        return source_type, source_key
    if event.event_type == QUESTIONNAIRE_SUBMITTED_EVENT_TYPE:
        questionnaire_id = _text(payload.get("questionnaire_id") or (payload.get("submission") or {}).get("questionnaire_id"))
        return "questionnaire_submission", f"questionnaire:{questionnaire_id}" if questionnaire_id else ""
    if event.event_type in PAYMENT_SUCCEEDED_EVENT_TYPES:
        product_code = _text(payload.get("product_code") or (payload.get("order") or {}).get("product_code"))
        return "payment", f"product:{product_code}" if product_code else ""
    if event.event_type == "channel_entry.entered":
        channel_id = _text(payload.get("channel_id"))
        return "channel_entry", f"channel:{channel_id}" if channel_id else ""
    if event.event_type == "external_form.submitted":
        form_id = _text(payload.get("form_id") or payload.get("source_key"))
        return "external_form", form_id
    return event.event_type, source_key
