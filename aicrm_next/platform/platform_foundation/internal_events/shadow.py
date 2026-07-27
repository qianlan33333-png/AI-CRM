from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from aicrm_next.platform.platform_foundation.command_bus import Command, CommandContext
from aicrm_next.platform.shared.safe_logging import safe_log_exception

from .config import (
    ai_campaign_internal_events_enabled,
    allowed_event_types,
    broadcast_task_internal_events_enabled,
    customer_tags_internal_events_enabled,
    event_type_allowed,
    ops_plan_internal_events_enabled,
    owner_migration_internal_events_enabled,
    questionnaire_internal_events_enabled,
)
from .consumer_registry import InternalEventConsumerRegistry, current_internal_event_consumer_registry
from .legacy_path_markers import mark_legacy_path_invoked
from .models import InternalEvent, InternalEventConsumerResult, InternalEventConsumerRun
from .customer_identity import register_customer_identity_event_consumers
from .service import InternalEventService

LOGGER = logging.getLogger(__name__)

QUESTIONNAIRE_SUBMITTED_EVENT_TYPE = "questionnaire.submitted"
CUSTOMER_TAGGED_EVENT_TYPE = "customer.tagged"
CUSTOMER_UNTAGGED_EVENT_TYPE = "customer.untagged"
AI_CAMPAIGN_CREATED_EVENT_TYPE = "ai_campaign.created"
AI_CAMPAIGN_APPROVED_EVENT_TYPE = "ai_campaign.approved"
AI_CAMPAIGN_STARTED_EVENT_TYPE = "ai_campaign.started"
BROADCAST_TASK_CREATED_EVENT_TYPE = "broadcast_task.created"
OPS_PLAN_APPROVED_EVENT_TYPE = "ops_plan.approved"
OWNER_MIGRATION_EXECUTED_EVENT_TYPE = "owner_migration.executed"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _redact_external_userid(external_userid: str) -> str:
    value = _text(external_userid)
    if not value:
        return ""
    if len(value) <= 8:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]}"


def _mask_mobile(value: Any) -> str:
    text = _text(value)
    if len(text) < 7:
        return "<redacted>" if text else ""
    return f"{text[:3]}****{text[-4:]}"


def _hash_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _safe_source_ref(value: Any, fallback: Any = "") -> str:
    text = _text(value)
    if text:
        return f"source_ref:{_hash_text(text)}"
    return _text(fallback)


def _safe_trace_ref(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return f"trace_ref:{_hash_text(text)}"


def _safe_ops_plan_ref(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return f"ops_plan_ref:{_hash_text(text)}"


def _safe_owner_ref(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return f"owner_ref:{_hash_text(text)}"


def _count_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_from(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _count_from(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    return _int_from(value)


def _first_count_source(sources: list[tuple[str, Any]]) -> tuple[int, str]:
    for source, value in sources:
        count = _count_from(value)
        if count is not None:
            return count, source
    return 0, "none"


def _owner_migration_count_diagnostics(
    *,
    result: dict[str, Any],
    update_counts: dict[str, Any],
    transfer: dict[str, Any],
    rows: list[Any],
    requested_external_userids: list[Any],
    touched_external_userids: list[Any],
    external_userids: list[str],
    transfer_success: Any,
) -> dict[str, Any]:
    failed_count, failed_count_source = _first_count_source(
        [
            ("result.failed_count", result.get("failed_count")),
            ("wecom_transfer.failed_count", transfer.get("failed_count")),
            ("wecom_transfer.failed_customers", transfer.get("failed_customers")),
            ("result.wecom_failed", result.get("wecom_failed")),
            ("result.crm_skipped_due_to_wecom_failure", result.get("crm_skipped_due_to_wecom_failure")),
        ]
    )
    customer_count, customer_count_source = _first_count_source(
        [
            ("result.candidate_count", result.get("candidate_count")),
            ("result.requested_count", result.get("requested_count")),
            ("result.requested_customer_scope", result.get("requested_external_userids")),
            ("result.eligible_count", result.get("eligible_count")),
            ("result.touched_count", result.get("touched_count")),
            ("requested_customer_scope", requested_external_userids or None),
            ("touched_customer_scope", touched_external_userids or None),
            ("row_customer_scope", external_userids or None),
            ("rows", rows or None),
            ("result.crm_updated", result.get("crm_updated")),
            ("update_counts.contacts", update_counts.get("contacts")),
        ]
    )
    explicit_success, explicit_success_source = _first_count_source(
        [
            ("result.success_count", result.get("success_count")),
            ("result.crm_updated", result.get("crm_updated")),
            ("update_counts.contacts", update_counts.get("contacts")),
            ("wecom_transfer.success_customer_scope", transfer_success),
        ]
    )
    has_explicit_success = explicit_success_source != "none"
    count_consistency = "ok"

    if has_explicit_success:
        success_count = explicit_success
        success_count_source = explicit_success_source
        if customer_count and failed_count and success_count + failed_count > customer_count:
            count_consistency = "explicit_success_count_exceeds_customer_count_with_failures"
    elif failed_count and customer_count:
        success_count = max(0, customer_count - failed_count)
        success_count_source = "customer_count_minus_failed_count"
        count_consistency = "all_failed" if success_count == 0 and failed_count >= customer_count else "inferred_from_customer_minus_failed"
    elif failed_count:
        success_count = 0
        success_count_source = "no_explicit_success_with_failures"
        count_consistency = "failed_without_customer_count"
    else:
        success_count, success_count_source = _first_count_source(
            [
                ("result.touched_count", result.get("touched_count")),
                ("touched_customer_scope", touched_external_userids or None),
                ("row_customer_scope", external_userids or None),
                ("customer_count", customer_count),
            ]
        )

    if not has_explicit_success and failed_count and customer_count and success_count + failed_count > customer_count:
        success_count = max(0, customer_count - failed_count)
        count_consistency = "corrected_success_count_to_customer_count"
        success_count_source = "customer_count_minus_failed_count"

    all_failed = bool(failed_count and customer_count and success_count == 0 and failed_count >= customer_count)
    return {
        "customer_count": customer_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "count_consistency": count_consistency,
        "count_source": {
            "customer_count": customer_count_source,
            "success_count": success_count_source,
            "failed_count": failed_count_source,
        },
        "partial_failure_present": bool(failed_count),
        "all_failed": all_failed,
    }


def _questionnaire_subject_id(submission: dict[str, Any]) -> str:
    external_userid = _text(submission.get("external_userid"))
    if external_userid:
        return _redact_external_userid(external_userid)
    respondent_key = _text(submission.get("respondent_key"))
    if respondent_key:
        return _redact_external_userid(respondent_key)
    return _mask_mobile(submission.get("mobile"))


def _source_context_source(source_context: dict[str, Any], fallback: str) -> str:
    return _text(source_context.get("source")) or _text(fallback)


def _mark_legacy_hook(
    event: InternalEvent,
    run: InternalEventConsumerRun,
    *,
    legacy_path: str,
    reason: str,
    severity: str = "info",
) -> None:
    mark_legacy_path_invoked(
        legacy_path=legacy_path,
        replacement_event_type=event.event_type,
        replacement_consumer=run.consumer_name,
        source_module="platform_foundation.internal_events.shadow",
        source_route=f"/internal-events/{event.event_type}/{run.consumer_name}",
        aggregate_id=event.aggregate_id or event.subject_id,
        reason=reason,
        severity=severity,
    )


def _skipped(reason: str, event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    return InternalEventConsumerResult(
        status="skipped",
        request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
        response_summary={"skipped": True, "reason": reason},
        result_summary={"reason": reason},
    )


def _succeeded_noop(reason: str, event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
        response_summary={"succeeded": True, "reason": reason},
        result_summary={"reason": reason},
    )


def questionnaire_webhook_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    return _skipped("questionnaire_webhook_shadow_only", event, run)


def questionnaire_tag_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    return _skipped("questionnaire_tag_shadow_only", event, run)


def automation_questionnaire_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    return _skipped("automation_questionnaire_not_configured", event, run)


def customer_summary_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    return _skipped("customer_summary_not_configured", event, run)


def tag_external_effect_shadow_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="customer_tag.legacy_side_effect_planning",
        reason="customer_tag_side_effect_replaced_by_internal_event_consumer",
    )
    payload = dict(event.payload_json or {})
    external_effect_job = payload.get("external_effect_job")
    side_effect_plan = payload.get("side_effect_plan")
    side_effect_plan_id = (
        side_effect_plan.get("id") or side_effect_plan.get("side_effect_plan_id")
        if isinstance(side_effect_plan, dict)
        else None
    )
    if isinstance(external_effect_job, dict) and external_effect_job.get("id"):
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={
                "external_effect_job_reused": True,
                "external_effect_job_created": False,
                "external_effect_job_id": external_effect_job.get("id"),
                "effect_type": _text(external_effect_job.get("effect_type")),
                "execution_mode": _text(external_effect_job.get("execution_mode") or "shadow"),
                "status": _text(external_effect_job.get("status") or "planned"),
                "real_external_call_executed": False,
                "wecom_api_called": False,
            },
            result_summary={
                "external_effect_job_reused": True,
                "external_effect_job_id": external_effect_job.get("id"),
                "reason": "customer_tag_external_effect_already_planned",
            },
        )
    if isinstance(side_effect_plan, dict) and side_effect_plan_id:
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={
                "side_effect_plan_reused": True,
                "side_effect_plan_id": side_effect_plan_id,
                "real_external_call_executed": False,
                "wecom_api_called": False,
            },
            result_summary={
                "side_effect_plan_reused": True,
                "side_effect_plan_id": side_effect_plan_id,
                "reason": "customer_tag_side_effect_already_planned",
            },
        )
    return _skipped("customer_tag_external_effect_not_configured_or_already_shadow_only", event, run)


def tag_summary_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    return _skipped("customer_tag_summary_not_configured", event, run)


def ai_assist_notify_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path=f"{event.event_type}.legacy_ai_assist_notify",
        reason="ai_assist_notify_replaced_by_internal_event_consumer",
    )
    return _skipped("ai_assist_notify_not_configured", event, run)


def broadcast_task_ai_assist_notify_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="broadcast_task.legacy_ai_assist_notify",
        reason="broadcast_ai_assist_notify_replaced_by_internal_event_consumer",
    )
    return _skipped("broadcast_task_ai_assist_notify_not_configured", event, run)


def legacy_broadcast_task_ai_assist_notify_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="broadcast_task.legacy_alias_ai_assist_notify",
        reason="legacy_alias_dispatch_only",
    )
    return _skipped("broadcast_task_legacy_ai_assist_notify_not_configured", event, run)


def ai_campaign_ai_assist_notify_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="ai_campaign.legacy_ai_assist_notify",
        reason="ai_campaign_notify_replaced_by_internal_event_consumer",
    )
    return _skipped("ai_campaign_ai_assist_notify_not_configured", event, run)


def ops_plan_ai_assist_notify_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="ops_plan.legacy_ai_assist_notify",
        reason="ops_plan_notify_replaced_by_internal_event_consumer",
    )
    return _skipped("ops_plan_ai_assist_notify_not_configured", event, run)


def legacy_ops_plan_ai_assist_notify_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="ops_plan.legacy_alias_ai_assist_notify",
        reason="legacy_alias_dispatch_only",
    )
    return _skipped("ops_plan_legacy_ai_assist_notify_not_configured", event, run)


def owner_migration_ai_assist_notify_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="owner_migration.legacy_ai_assist_notify",
        reason="owner_migration_notify_replaced_by_internal_event_consumer",
    )
    return _skipped("owner_migration_ai_assist_notify_not_configured", event, run)


def legacy_owner_migration_ai_assist_notify_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="owner_migration.legacy_alias_ai_assist_notify",
        reason="legacy_alias_dispatch_only",
    )
    return _skipped("owner_migration_legacy_ai_assist_notify_not_configured", event, run)


def campaign_summary_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    return _skipped("campaign_summary_not_configured", event, run)


def _ops_plan_id_from_event(event: InternalEvent) -> str:
    payload_summary = event.payload_summary_json if isinstance(event.payload_summary_json, dict) else {}
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    plan_payload = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    for value in (
        payload_summary.get("plan_id"),
        payload.get("plan_id"),
        plan_payload.get("plan_id"),
        event.aggregate_id if event.aggregate_type == "cloud_orchestrator_plan" else "",
        event.subject_id if event.subject_type == "ops_plan" else "",
        event.source_command_id,
    ):
        if _text(value):
            return _text(value)
    return ""


def _ops_plan_type_from_event(event: InternalEvent) -> str:
    payload_summary = event.payload_summary_json if isinstance(event.payload_summary_json, dict) else {}
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    return _text(payload_summary.get("plan_type") or payload.get("plan_type") or "cloud_plan")


def broadcast_task_planner_consumer(
    event: InternalEvent,
    run: InternalEventConsumerRun,
    *,
    repository_factory: Callable[[], Any] | None = None,
) -> InternalEventConsumerResult:
    plan_id = _ops_plan_id_from_event(event)
    plan_type = _ops_plan_type_from_event(event)
    request_summary = {
        "event_id": event.event_id,
        "consumer_name": run.consumer_name,
        "plan_id": plan_id or "missing",
    }
    if not plan_id:
        return InternalEventConsumerResult(
            status="skipped",
            request_summary=request_summary,
            response_summary={"skipped": True, "reason": "missing_plan_id", "real_external_call_executed": False},
            result_summary={"reason": "missing_plan_id", "planner_result": "planner_skipped_missing_required_input"},
        )
    if plan_type not in {"cloud_plan", "ops_plan"}:
        return InternalEventConsumerResult(
            status="skipped",
            request_summary={**request_summary, "plan_type": plan_type},
            response_summary={
                "skipped": True,
                "reason": "consumer_non_applicable",
                "plan_type": plan_type,
                "real_external_call_executed": False,
            },
            result_summary={
                "reason": "consumer_non_applicable",
                "plan_type": plan_type,
                "planner_result": "planner_skipped_non_applicable",
            },
        )
    if repository_factory is None:
        return InternalEventConsumerResult(
            status="failed_terminal",
            request_summary=request_summary,
            response_summary={
                "error": "broadcast planner composition missing",
                "real_external_call_executed": False,
            },
            result_summary={"reason": "broadcast_planner_composition_missing"},
            error_code="broadcast_planner_composition_missing",
            error_message="broadcast planner composition missing",
        )
    result = repository_factory().create_or_reuse_plan_broadcast_job(
        plan_id,
        operator=event.actor_id or "internal_event_worker",
        source_event_id=event.event_id,
        idempotency_key=event.idempotency_key,
    )
    if _text(result.get("status")) == "skipped":
        reason = _text(result.get("reason")) or "planner_skipped_missing_required_input"
        planner_result = (
            "planner_skipped_non_applicable"
            if reason in {"consumer_non_applicable", "unsupported_plan_type"}
            else "planner_skipped_missing_required_input"
        )
        return InternalEventConsumerResult(
            status="skipped",
            request_summary=request_summary,
            response_summary={"skipped": True, "reason": reason, "real_external_call_executed": False},
            result_summary={"reason": reason, "planner_result": planner_result},
        )
    duplicate_handling = "reused" if _text(result.get("status")) == "reused" else "created"
    planner_result = "planner_reused_broadcast_job" if duplicate_handling == "reused" else "planner_created_broadcast_job"
    response_summary = {
        "succeeded": True,
        "planner_result": planner_result,
        "duplicate_handling": duplicate_handling,
        "broadcast_job_id": int(result.get("broadcast_job_id") or 0),
        "broadcast_job_count": int(result.get("broadcast_job_count") or (1 if result.get("broadcast_job_id") else 0)),
        "created_count": int(result.get("created_count") or 0),
        "reused_count": int(result.get("reused_count") or 0),
        "push_center_job_id": _text(result.get("push_center_job_id")),
        "downstream_status": _text(result.get("downstream_status")) or "broadcast_job_queued",
        "idempotency_key": _text(result.get("idempotency_key")),
        "target_count": int(result.get("target_count") or 0),
        "real_external_call_executed": False,
        "external_effect_job_created": False,
    }
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary=request_summary,
        response_summary=response_summary,
        result_summary=response_summary,
    )


def broadcast_queue_projection_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    if event.event_type == BROADCAST_TASK_CREATED_EVENT_TYPE:
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={
                "succeeded": True,
                "broadcast_queue_projection": "broadcast_task_created_recorded",
                "real_external_call_executed": False,
            },
            result_summary={"broadcast_queue_projection": "broadcast_task_created_recorded"},
        )
    return _succeeded_noop("broadcast_queue_projection_shadow_only", event, run)


def push_center_link_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    if event.event_type == BROADCAST_TASK_CREATED_EVENT_TYPE:
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={
                "succeeded": True,
                "push_center_link": "shadow_only",
                "real_external_call_executed": False,
            },
            result_summary={"push_center_link": "shadow_only"},
        )
    return _succeeded_noop("push_center_link_shadow_only", event, run)


def automation_schedule_refresh_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="ops_plan.legacy_automation_schedule_refresh",
        reason="automation_schedule_refresh_replaced_by_internal_event_consumer",
    )
    if event.event_type == OPS_PLAN_APPROVED_EVENT_TYPE:
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={
                "succeeded": True,
                "automation_schedule_refresh": "shadow_only",
                "reason": "automation_schedule_refresh_shadow_only",
            },
            result_summary={
                "automation_schedule_refresh": "shadow_only",
                "reason": "automation_schedule_refresh_shadow_only",
            },
        )
    return _succeeded_noop("automation_schedule_refresh_shadow_only", event, run)


def audit_projection_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    if event.event_type == OPS_PLAN_APPROVED_EVENT_TYPE:
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={
                "succeeded": True,
                "audit_projection": "ops_plan_approved_recorded",
            },
            result_summary={"audit_projection": "ops_plan_approved_recorded"},
        )
    if event.event_type == BROADCAST_TASK_CREATED_EVENT_TYPE:
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={
                "succeeded": True,
                "audit_projection": "broadcast_task_created_recorded",
                "real_external_call_executed": False,
            },
            result_summary={"audit_projection": "broadcast_task_created_recorded"},
        )
    return _succeeded_noop("audit_projection_shadow_only", event, run)


def customer_owner_projection_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    if event.event_type == OWNER_MIGRATION_EXECUTED_EVENT_TYPE:
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={
                "succeeded": True,
                "customer_owner_projection": "owner_migration_recorded",
                "real_external_call_executed": False,
            },
            result_summary={"customer_owner_projection": "owner_migration_recorded"},
        )
    return _succeeded_noop("customer_owner_projection_shadow_only", event, run)


def customer_summary_mark_dirty_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    if event.event_type == OWNER_MIGRATION_EXECUTED_EVENT_TYPE:
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={
                "succeeded": True,
                "customer_summary_mark_dirty": "owner_migration_recorded",
                "real_external_call_executed": False,
            },
            result_summary={"customer_summary_mark_dirty": "owner_migration_recorded"},
        )
    return _succeeded_noop("customer_summary_mark_dirty_shadow_only", event, run)


def webhook_owner_migration_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="owner_migration.legacy_webhook_notify",
        reason="owner_migration_webhook_replaced_by_internal_event_consumer",
    )
    return _skipped("owner_migration_webhook_not_configured", event, run)


def register_shadow_event_consumers(
    registry: InternalEventConsumerRegistry | None = None,
    *,
    broadcast_task_planner_handler: Any | None = None,
) -> None:
    registry = registry or current_internal_event_consumer_registry()
    register_customer_identity_event_consumers(registry)

    for event_type in (CUSTOMER_TAGGED_EVENT_TYPE, CUSTOMER_UNTAGGED_EVENT_TYPE):
        registry.register(event_type, "tag_external_effect_shadow_consumer", tag_external_effect_shadow_consumer, consumer_type="external_effect_planner")
        registry.register(event_type, "tag_summary_consumer", tag_summary_consumer, consumer_type="projection")
        registry.register(event_type, "ai_assist_notify_consumer", ai_assist_notify_consumer, consumer_type="orchestration")

    for event_type in (AI_CAMPAIGN_CREATED_EVENT_TYPE, AI_CAMPAIGN_APPROVED_EVENT_TYPE, AI_CAMPAIGN_STARTED_EVENT_TYPE):
        registry.register(event_type, "ai_campaign_ai_assist_notify_consumer", ai_campaign_ai_assist_notify_consumer, consumer_type="orchestration")
        registry.register(event_type, "campaign_summary_consumer", campaign_summary_consumer, consumer_type="projection")
        registry.register(
            event_type,
            "broadcast_task_planner_consumer",
            broadcast_task_planner_handler or broadcast_task_planner_consumer,
            consumer_type="orchestration",
        )
        registry.register(event_type, "audit_projection_consumer", audit_projection_consumer, consumer_type="projection")

    registry.register(BROADCAST_TASK_CREATED_EVENT_TYPE, "broadcast_queue_projection_consumer", broadcast_queue_projection_consumer, consumer_type="projection")
    registry.register(BROADCAST_TASK_CREATED_EVENT_TYPE, "push_center_link_consumer", push_center_link_consumer, consumer_type="projection")
    registry.register(BROADCAST_TASK_CREATED_EVENT_TYPE, "broadcast_task_ai_assist_notify_consumer", broadcast_task_ai_assist_notify_consumer, consumer_type="orchestration")
    registry.register(BROADCAST_TASK_CREATED_EVENT_TYPE, "audit_projection_consumer", audit_projection_consumer, consumer_type="projection")
    registry.register_handler_alias(BROADCAST_TASK_CREATED_EVENT_TYPE, "ai_assist_notify_consumer", legacy_broadcast_task_ai_assist_notify_consumer)

    registry.register(OPS_PLAN_APPROVED_EVENT_TYPE, "automation_schedule_refresh_consumer", automation_schedule_refresh_consumer, consumer_type="orchestration")
    registry.register(OPS_PLAN_APPROVED_EVENT_TYPE, "ops_plan_ai_assist_notify_consumer", ops_plan_ai_assist_notify_consumer, consumer_type="orchestration")
    registry.register(OPS_PLAN_APPROVED_EVENT_TYPE, "audit_projection_consumer", audit_projection_consumer, consumer_type="projection")
    registry.register(
        OPS_PLAN_APPROVED_EVENT_TYPE,
        "broadcast_task_planner_consumer",
        broadcast_task_planner_handler or broadcast_task_planner_consumer,
        consumer_type="orchestration",
    )
    registry.register_handler_alias(OPS_PLAN_APPROVED_EVENT_TYPE, "ai_assist_notify_consumer", legacy_ops_plan_ai_assist_notify_consumer)

    registry.register(OWNER_MIGRATION_EXECUTED_EVENT_TYPE, "customer_owner_projection_consumer", customer_owner_projection_consumer, consumer_type="projection")
    registry.register(OWNER_MIGRATION_EXECUTED_EVENT_TYPE, "customer_summary_mark_dirty_consumer", customer_summary_mark_dirty_consumer, consumer_type="projection")
    registry.register(OWNER_MIGRATION_EXECUTED_EVENT_TYPE, "owner_migration_ai_assist_notify_consumer", owner_migration_ai_assist_notify_consumer, consumer_type="orchestration")
    registry.register(OWNER_MIGRATION_EXECUTED_EVENT_TYPE, "webhook_owner_migration_consumer", webhook_owner_migration_consumer, consumer_type="external_effect_planner")
    registry.register_handler_alias(OWNER_MIGRATION_EXECUTED_EVENT_TYPE, "ai_assist_notify_consumer", legacy_owner_migration_ai_assist_notify_consumer)


def emit_questionnaire_submitted_shadow_event(
    *,
    command: Command,
    questionnaire: dict[str, Any],
    submission: dict[str, Any],
    score: int,
    final_tags: list[str],
) -> dict[str, Any]:
    if not questionnaire_internal_events_enabled():
        return {"status": "skipped", "reason": "questionnaire_internal_events_disabled"}
    if not event_type_allowed(QUESTIONNAIRE_SUBMITTED_EVENT_TYPE):
        return {"status": "skipped", "reason": "internal_events_disabled_or_event_type_not_allowed"}
    submission_id = _text(submission.get("submission_id"))
    if not submission_id:
        return {"status": "skipped", "reason": "submission_id_missing"}
    answer_snapshots = [
        dict(item)
        for item in (submission.get("answer_snapshots") or [])
        if isinstance(item, dict)
    ]
    answer_count = len(answer_snapshots) if answer_snapshots else len(dict(submission.get("answers") or {}))
    external_push_config = dict(questionnaire.get("external_push_config") or {})
    external_push_summary = {
        "enabled": bool(external_push_config.get("enabled") or questionnaire.get("external_push_enabled")),
        "target_url_present": bool(_text(external_push_config.get("webhook_url") or questionnaire.get("external_push_url"))),
        "type": _text(external_push_config.get("type") or questionnaire.get("external_push_type")),
    }
    result = InternalEventService().emit_event(
        event_type=QUESTIONNAIRE_SUBMITTED_EVENT_TYPE,
        event_version=1,
        aggregate_type="questionnaire_submission",
        aggregate_id=submission_id,
        subject_type="customer",
        subject_id=_questionnaire_subject_id(submission),
        idempotency_key=f"questionnaire.submitted:{submission_id}",
        source_module="questionnaire.h5_write",
        source_command_id=command.command_id,
        correlation_id=command.idempotency_key or command.command_id,
        context=CommandContext(
            actor_id=command.context.actor_id,
            actor_type=command.context.actor_type,
            trace_id=command.context.trace_id,
            request_id=command.command_id,
            source_route=command.context.source_route,
            dry_run=command.context.dry_run,
        ),
        payload={
            "questionnaire": {
                "id": questionnaire.get("id"),
                "slug": questionnaire.get("slug"),
                "title": questionnaire.get("title") or questionnaire.get("name"),
                "external_push_config": external_push_config,
                "external_push_enabled": bool(questionnaire.get("external_push_enabled")),
                "external_push_url": questionnaire.get("external_push_url") or "",
            },
            "submission": {
                "submission_id": submission_id,
                "questionnaire_id": int(questionnaire.get("id") or submission.get("questionnaire_id") or 0),
                "slug": questionnaire.get("slug") or submission.get("slug") or "",
                "respondent_key": submission.get("respondent_key") or "",
                "external_userid": submission.get("external_userid") or "",
                "openid_present": bool(_text(submission.get("openid"))),
                "unionid_present": bool(_text(submission.get("unionid"))),
                "mobile_present": bool(_text(submission.get("mobile"))),
                "person_id": submission.get("person_id"),
                "binding_status": submission.get("binding_status") or "",
                "submitted_at": submission.get("submitted_at") or submission.get("created_at") or "",
                "created_at": submission.get("created_at") or "",
                "score": int(score or 0),
                "answer_count": answer_count,
            },
            "answer_snapshots": answer_snapshots,
            "score": score,
            "final_tags": list(final_tags or []),
            "external_push": external_push_summary,
            "source": {
                "source_route": command.context.source_route,
                "trace_id": command.context.trace_id,
                "command_id": command.command_id,
            },
        },
        payload_summary={
            "questionnaire_id": int(questionnaire.get("id") or 0),
            "slug": _text(questionnaire.get("slug")),
            "submission_id": submission_id,
            "external_userid_present": bool(_text(submission.get("external_userid"))),
            "mobile_present": bool(_text(submission.get("mobile"))),
            "answer_count": answer_count,
            "score": int(score or 0),
            "final_tag_count": len(final_tags or []),
        },
    )
    return {"status": "emitted", "event_id": result["event"]["event_id"], "consumer_run_count": len(result.get("consumer_runs") or [])}


def emit_customer_tag_shadow_event(
    *,
    command: Command,
    effect_type: str,
    external_userid: str,
    tag_ids: list[str],
    source_context: dict[str, Any],
    side_effect_plan: dict[str, Any] | None = None,
    external_effect_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_type = CUSTOMER_UNTAGGED_EVENT_TYPE if _text(effect_type) == "wecom.tag.unmark" else CUSTOMER_TAGGED_EVENT_TYPE
    if not customer_tags_internal_events_enabled():
        return {"status": "skipped", "reason": "customer_tags_internal_events_disabled"}
    if not event_type_allowed(event_type):
        return {"status": "skipped", "reason": "internal_events_disabled_or_event_type_not_allowed"}
    if not _text(external_userid):
        return {"status": "skipped", "reason": "external_userid_missing"}
    stable_key = command.idempotency_key or command.command_id or f"{event_type}:{external_userid}:{','.join(tag_ids)}"
    source = _source_context_source(source_context, command.context.source_route)
    normalized_tags = list(tag_ids or [])
    external_effect_summary = dict(external_effect_job or {}) if isinstance(external_effect_job, dict) else {}
    side_effect_summary = dict(side_effect_plan or {}) if isinstance(side_effect_plan, dict) else {}
    result = InternalEventService().emit_event(
        event_type=event_type,
        event_version=1,
        aggregate_type="customer",
        aggregate_id=_text(external_userid),
        subject_type="customer",
        subject_id=_redact_external_userid(external_userid),
        idempotency_key=f"{event_type}:{stable_key}",
        source_module="customer_tags.live_mutation",
        source_command_id=command.command_id,
        correlation_id=command.idempotency_key or command.command_id,
        context=command.context,
        payload={
            "external_userid": external_userid,
            "tag_ids": normalized_tags,
            "tag_count": len(normalized_tags),
            "source_context": dict(source_context or {}),
            "effect_type": effect_type,
            "source": {
                "source_module": "customer_tags.live_mutation",
                "source_route": command.context.source_route,
                "command_id": command.command_id,
                "trace_id": command.context.trace_id,
            },
            "side_effect_plan": {
                "id": side_effect_summary.get("id") or side_effect_summary.get("side_effect_plan_id"),
                "effect_type": side_effect_summary.get("effect_type"),
                "status": side_effect_summary.get("status"),
            } if side_effect_summary else {},
            "external_effect_job": {
                "id": external_effect_summary.get("id"),
                "effect_type": external_effect_summary.get("effect_type"),
                "status": external_effect_summary.get("status"),
                "execution_mode": external_effect_summary.get("execution_mode"),
                "idempotency_key": external_effect_summary.get("idempotency_key"),
            } if external_effect_summary else {},
        },
        payload_summary={
            "external_userid_redacted": _redact_external_userid(external_userid),
            "tag_count": len(normalized_tags),
            "tag_ids_count": len(normalized_tags),
            "source": source,
            "effect_type": effect_type,
        },
    )
    return {"status": "emitted", "event_id": result["event"]["event_id"], "consumer_run_count": len(result.get("consumer_runs") or [])}


def emit_ai_campaign_shadow_event(
    *,
    command: Command,
    event_type: str,
    campaign: dict[str, Any],
) -> dict[str, Any]:
    if not ai_campaign_internal_events_enabled():
        return {"status": "skipped", "reason": "ai_campaign_internal_events_disabled"}
    if not event_type_allowed(event_type):
        return {"status": "skipped", "reason": "internal_events_disabled_or_event_type_not_allowed"}
    campaign_code = _text(campaign.get("campaign_code") or command.payload.get("campaign_code"))
    if not campaign_code:
        return {"status": "skipped", "reason": "campaign_code_missing"}
    status = _text(campaign.get("run_status") or campaign.get("status"))
    review_status = _text(campaign.get("review_status"))
    event_version_key = _ai_campaign_event_version_key(event_type=event_type, campaign=campaign)
    operator = _text(command.context.actor_id)
    target_count = int(campaign.get("member_count") or campaign.get("target_count") or campaign.get("audience_count") or 0)
    objective = _text(campaign.get("intent") or campaign.get("objective"))
    created_at = _text(campaign.get("created_at"))
    approved_at = _text(campaign.get("approved_at"))
    started_at = _text(campaign.get("started_at"))
    result = InternalEventService().emit_event(
        event_type=event_type,
        event_version=1,
        aggregate_type="ai_campaign",
        aggregate_id=campaign_code,
        subject_type="ai_campaign",
        subject_id=campaign_code,
        idempotency_key=f"{event_type}:{campaign_code}:{event_version_key}",
        source_module="cloud_orchestrator.campaigns_write",
        source_command_id=command.command_id,
        correlation_id=command.idempotency_key or command.command_id,
        context=command.context,
        payload={
            "campaign": {
                "campaign_code": campaign_code,
                "campaign_id": campaign.get("id"),
                "status": status,
                "review_status": review_status,
                "run_status": status,
                "display_name": _text(campaign.get("display_name")),
                "objective": objective,
                "objective_present": bool(objective),
                "target_count": target_count,
                "audience_count": target_count,
                "operator": operator,
                "created_at": created_at,
                "approved_at": approved_at,
                "started_at": started_at,
                "trace_id": _text(campaign.get("trace_id") or command.context.trace_id),
                "metadata": _safe_campaign_metadata(campaign.get("metadata") or campaign.get("metadata_json")),
            },
            "source": {
                "source_module": "cloud_orchestrator.campaigns_write",
                "source_route": command.context.source_route,
                "command_id": command.command_id,
                "trace_id": command.context.trace_id,
            },
        },
        payload_summary={
            "campaign_code": campaign_code,
            "status": status or review_status,
            "review_status": review_status,
            "run_status": status,
            "operator": operator,
            "source": command.context.source_route,
            "target_count": target_count,
            "objective_present": bool(objective),
            "approved": event_type == AI_CAMPAIGN_APPROVED_EVENT_TYPE or review_status == "approved",
            "started": event_type == AI_CAMPAIGN_STARTED_EVENT_TYPE or status == "active",
        },
    )
    return {"status": "emitted", "event_id": result["event"]["event_id"], "consumer_run_count": len(result.get("consumer_runs") or [])}


def _ai_campaign_event_version_key(*, event_type: str, campaign: dict[str, Any]) -> str:
    if event_type == AI_CAMPAIGN_CREATED_EVENT_TYPE:
        return "created"
    if event_type == AI_CAMPAIGN_APPROVED_EVENT_TYPE:
        return _text(campaign.get("approved_at") or campaign.get("approval_version") or "approved")
    if event_type == AI_CAMPAIGN_STARTED_EVENT_TYPE:
        return _text(campaign.get("started_at") or campaign.get("start_version") or "started")
    return "v1"


def _safe_campaign_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {}
    for key in ("group_code", "group_label", "source", "source_type", "business_domain"):
        text = _text(value.get(key))
        if text:
            allowed[key] = text
    return allowed


def emit_broadcast_task_created_shadow_event(
    *,
    job: dict[str, Any],
    source_module: str,
    source_route: str = "",
    operator: str = "",
    source: str = "",
) -> dict[str, Any]:
    if not broadcast_task_internal_events_enabled():
        return {"status": "skipped", "reason": "broadcast_task_internal_events_disabled"}
    configured_event_types = allowed_event_types()
    if not configured_event_types or BROADCAST_TASK_CREATED_EVENT_TYPE not in set(configured_event_types):
        return {"status": "skipped", "reason": "broadcast_task_event_type_not_explicitly_allowed"}
    if not event_type_allowed(BROADCAST_TASK_CREATED_EVENT_TYPE, configured=configured_event_types):
        return {"status": "skipped", "reason": "internal_events_disabled_or_event_type_not_allowed"}
    job_id = _text(job.get("id") or job.get("job_id") or job.get("broadcast_job_id"))
    if not job_id:
        return {"status": "skipped", "reason": "broadcast_job_id_missing"}
    raw_trace_id = _text(job.get("trace_id"))
    raw_idempotency_key = _text(job.get("idempotency_key"))
    original_trace_hash = _hash_text(raw_trace_id)
    original_idempotency_key_hash = _hash_text(raw_idempotency_key)
    safe_event_ref = f"broadcast_task.created:{job_id}"
    trace_id = safe_event_ref
    correlation_id = safe_event_ref
    source_type = _text(source or job.get("source_type") or source_module)
    source_id = _text(job.get("source_id"))
    safe_source_ref = _safe_source_ref(source_id)
    safe_source_hash = _hash_text(source_id)
    safe_command_id = safe_event_ref
    batch_id = _text(job.get("batch_key") or job_id)
    scheduled_at = _text(job.get("scheduled_at") or job.get("scheduled_for"))
    target_count = int(job.get("target_count") or job.get("audience_count") or 0)
    send_channel = _text(job.get("send_channel") or job.get("channel"))
    task_type = _text(job.get("task_type") or job.get("content_type") or job.get("source_type") or "broadcast_task")
    campaign_code = _text(job.get("campaign_code") or job.get("campaign_id"))
    ops_plan_id = _text(job.get("ops_plan_id") or job.get("plan_id"))
    if not ops_plan_id and source_type.startswith("cloud_plan"):
        ops_plan_id = batch_id.removeprefix("cloud_plan_recipient:")
    safe_ops_plan_ref = _safe_ops_plan_ref(ops_plan_id)
    safe_ops_plan_hash = _hash_text(ops_plan_id)
    safe_job_payload = {
        "task_id": job_id,
        "task_code": _text(job.get("task_code") or job.get("code")),
        "source_type": _text(job.get("source_type")),
        "source_module": source_module,
        "source_route": source_route,
        "source_id": safe_source_ref,
        "source_id_redacted": safe_source_ref,
        "source_id_hash": safe_source_hash,
        "source_id_present": bool(source_id),
        "related_campaign_code": campaign_code,
        "related_ops_plan_id": safe_ops_plan_ref,
        "related_ops_plan_ref": safe_ops_plan_ref,
        "related_ops_plan_hash": safe_ops_plan_hash,
        "related_ops_plan_present": bool(ops_plan_id),
        "task_type": task_type,
        "send_channel": send_channel,
        "target_count": target_count,
        "audience_count": target_count,
        "created_by": _text(operator or job.get("created_by")),
        "scheduled_at": scheduled_at,
        "status": _text(job.get("status") or "created"),
        "trace_id": trace_id,
        "original_trace_ref": _safe_trace_ref(raw_trace_id),
        "original_trace_present": bool(raw_trace_id),
        "original_trace_hash": original_trace_hash,
        "trace_id_present": bool(raw_trace_id),
        "trace_id_hash": original_trace_hash,
        "original_idempotency_key_present": bool(raw_idempotency_key),
        "original_idempotency_key_hash": original_idempotency_key_hash,
        "idempotency_key_present": bool(raw_idempotency_key),
        "idempotency_key_hash": original_idempotency_key_hash,
        "command_id": safe_command_id,
        "content_summary_present": bool(_text(job.get("content_summary"))),
        "target_external_userids_count": len(job.get("target_external_userids") or []) if isinstance(job.get("target_external_userids"), list) else target_count,
    }
    result = InternalEventService().emit_event(
        event_type=BROADCAST_TASK_CREATED_EVENT_TYPE,
        event_version=1,
        aggregate_type="broadcast_task",
        aggregate_id=job_id,
        subject_type="broadcast_task",
        subject_id=job_id,
        idempotency_key=f"broadcast_task.created:{job_id}",
        source_module=source_module,
        source_command_id=safe_command_id,
        correlation_id=correlation_id,
        context=CommandContext(
            actor_id=_text(operator or job.get("created_by")),
            actor_type="admin",
            trace_id=trace_id,
            request_id=f"broadcast_task.created:{job_id}",
            source_route=source_route,
        ),
        payload={"broadcast_task": safe_job_payload},
        payload_summary={
            "task_id": job_id,
            "task_type": task_type,
            "send_channel": send_channel,
            "source": source_type,
            "campaign_code": campaign_code,
            "ops_plan_id": safe_ops_plan_ref,
            "ops_plan_ref": safe_ops_plan_ref,
            "ops_plan_hash": safe_ops_plan_hash,
            "ops_plan_present": bool(ops_plan_id),
            "target_count": target_count,
            "status": _text(job.get("status") or "created"),
            "scheduled": bool(scheduled_at),
        },
    )
    return {"status": "emitted", "event_id": result["event"]["event_id"], "consumer_run_count": len(result.get("consumer_runs") or [])}


def emit_ops_plan_approved_shadow_event(
    *,
    plan: dict[str, Any],
    stats: dict[str, Any] | None = None,
    operator: str = "",
    aggregate_type: str = "cloud_orchestrator_plan",
    source_module: str = "cloud_orchestrator.application",
    source_route: str = "",
) -> dict[str, Any]:
    if not ops_plan_internal_events_enabled():
        return {"status": "skipped", "reason": "ops_plan_internal_events_disabled"}
    configured_event_types = allowed_event_types()
    if not configured_event_types or OPS_PLAN_APPROVED_EVENT_TYPE not in set(configured_event_types):
        return {"status": "skipped", "reason": "ops_plan_event_type_not_explicitly_allowed"}
    if not event_type_allowed(OPS_PLAN_APPROVED_EVENT_TYPE, configured=configured_event_types):
        return {"status": "skipped", "reason": "internal_events_disabled_or_event_type_not_allowed"}
    plan_id = _text(plan.get("plan_id") or plan.get("id") or plan.get("code"))
    if not plan_id:
        return {"status": "skipped", "reason": "plan_id_missing"}
    source = _text(plan.get("source_type") or plan.get("business_domain") or "cloud_orchestrator")
    trace_id = _text(plan.get("trace_id") or plan_id)
    stats = dict(stats or {})
    review_status = _text(plan.get("review_status") or plan.get("approval_status"))
    run_status = _text(plan.get("run_status") or plan.get("status"))
    approved_at = _text(plan.get("approved_at"))
    approved_marker = _text(plan.get("approved_version") or approved_at or "approved")
    target_count = int(stats.get("target_count") or plan.get("target_count") or plan.get("candidate_count") or 0)
    campaign_code = _text(plan.get("campaign_code") or plan.get("campaign_id"))
    plan_type = _text(plan.get("plan_type") or plan.get("source_type") or aggregate_type)
    stage = review_status or run_status or "approved"
    service = InternalEventService()
    legacy_idempotency_key = f"ops_plan.approved:{aggregate_type}:{plan_id}"
    legacy_events, legacy_total = service.list_events({"idempotency_key": legacy_idempotency_key}, limit=1)
    if legacy_total and legacy_events:
        legacy_event = legacy_events[0]
        _legacy_runs, run_total = service.list_consumer_runs({"event_id": legacy_event.event_id})
        return {
            "status": "emitted",
            "event_id": legacy_event.event_id,
            "consumer_run_count": run_total,
            "reason": "ops_plan_legacy_idempotency_key_reused",
            "legacy_idempotency_key_reused": True,
        }
    result = service.emit_event(
        event_type=OPS_PLAN_APPROVED_EVENT_TYPE,
        event_version=1,
        aggregate_type=aggregate_type,
        aggregate_id=plan_id,
        subject_type="ops_plan",
        subject_id=plan_id,
        idempotency_key=f"ops_plan.approved:{aggregate_type}:{plan_id}:{approved_marker}",
        source_module=source_module,
        source_command_id=plan_id,
        correlation_id=trace_id,
        context=CommandContext(
            actor_id=_text(operator),
            actor_type="admin",
            trace_id=trace_id,
            request_id=f"ops_plan.approved:{plan_id}",
            source_route=source_route,
        ),
        payload={
            "plan": {
                "plan_id": plan_id,
                "plan_code": _text(plan.get("plan_code") or plan.get("code")),
                "approval_status": review_status,
                "review_status": review_status,
                "run_status": run_status,
                "operator": _text(operator),
                "source": source,
                "target_count": target_count,
                "audience_count": target_count,
                "campaign_code": campaign_code,
                "approved_at": approved_at,
                "plan_type": plan_type,
                "stage": stage,
                "status": review_status or run_status,
                "plan_summary": {
                    "display_name_present": bool(_text(plan.get("display_name"))),
                    "source_type": source,
                },
            },
            "source": {
                "source_module": source_module,
                "source_route": source_route,
                "command_id": plan_id,
                "trace_id": trace_id,
            },
        },
        payload_summary={
            "plan_id": plan_id,
            "source": source,
            "operator": _text(operator),
            "target_count": target_count,
            "campaign_code": campaign_code,
            "approved": True,
            "plan_type": plan_type,
            "stage": stage,
            "status": review_status or run_status,
        },
    )
    return {"status": "emitted", "event_id": result["event"]["event_id"], "consumer_run_count": len(result.get("consumer_runs") or [])}


def emit_owner_migration_executed_shadow_event(
    *,
    command: Any,
    result: dict[str, Any],
    source_route: str = "/api/admin/owner-migration/execute",
) -> dict[str, Any]:
    if not owner_migration_internal_events_enabled():
        return {"status": "skipped", "reason": "owner_migration_internal_events_disabled"}
    configured_event_types = allowed_event_types()
    if not configured_event_types or OWNER_MIGRATION_EXECUTED_EVENT_TYPE not in set(configured_event_types):
        return {"status": "skipped", "reason": "owner_migration_event_type_not_explicitly_allowed"}
    if not event_type_allowed(OWNER_MIGRATION_EXECUTED_EVENT_TYPE, configured=configured_event_types):
        return {"status": "skipped", "reason": "internal_events_disabled_or_event_type_not_allowed"}
    result_id = _text(result.get("result_id") or result.get("job_id") or result.get("preview_token"))
    if not result_id:
        return {"status": "skipped", "reason": "owner_migration_result_id_missing"}
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    external_userids = [
        _text(row.get("external_userid"))
        for row in rows
        if isinstance(row, dict) and _text(row.get("external_userid"))
    ]
    requested_external_userids = _count_list(result.get("requested_external_userids"))
    touched_external_userids = _count_list(result.get("touched_external_userids"))
    transfer = result.get("wecom_transfer") if isinstance(result.get("wecom_transfer"), dict) else {}
    transfer_success = transfer.get("success_external_userids") if "success_external_userids" in transfer else None
    if transfer_success is not None and not isinstance(transfer_success, list):
        transfer_success = None

    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    update_counts = result.get("update_counts") if isinstance(result.get("update_counts"), dict) else {}
    counts = _owner_migration_count_diagnostics(
        result=result,
        update_counts=update_counts,
        transfer=transfer,
        rows=rows,
        requested_external_userids=requested_external_userids,
        touched_external_userids=touched_external_userids,
        external_userids=external_userids,
        transfer_success=transfer_success,
    )
    customer_count = int(counts["customer_count"])
    success_count = int(counts["success_count"])
    failed_count = int(counts["failed_count"])
    count_consistency = _text(counts.get("count_consistency"))
    count_source = counts.get("count_source") if isinstance(counts.get("count_source"), dict) else {}
    partial_failure_present = bool(counts.get("partial_failure_present"))
    all_failed = bool(counts.get("all_failed"))
    skipped_count = _safe_int(result.get("skipped_count"))
    operator = _text(result.get("operator") or getattr(command, "operator", ""))
    source_owner_userid = _text(result.get("source_owner_userid") or getattr(command, "source_owner_userid", ""))
    target_owner_userid = _text(result.get("target_owner_userid") or getattr(command, "target_owner_userid", ""))
    source_owner_hash = _hash_text(source_owner_userid)
    target_owner_hash = _hash_text(target_owner_userid)
    source_command_id = f"owner_migration.executed:{result_id}"
    trace_id = source_command_id
    customer_scope_hash = _hash_text("|".join(sorted(external_userids or touched_external_userids or requested_external_userids)))
    result_payload = InternalEventService().emit_event(
        event_type=OWNER_MIGRATION_EXECUTED_EVENT_TYPE,
        event_version=1,
        aggregate_type="owner_migration",
        aggregate_id=result_id,
        subject_type="owner_migration",
        subject_id=result_id,
        idempotency_key=f"owner_migration.executed:{result_id}",
        source_module="owner_migration.application",
        source_command_id=source_command_id,
        correlation_id=source_command_id,
        context=CommandContext(
            actor_id=operator,
            actor_type="admin",
            trace_id=trace_id,
            request_id=f"owner_migration.executed:{result_id}",
            source_route=source_route,
        ),
        payload={
            "owner_migration": {
                "migration_id": result_id,
                "batch_id": result_id,
                "execution_id": result_id,
                "job_id": _text(result.get("job_id")),
                "session_id": _safe_source_ref(result.get("session_id") or getattr(command, "session_id", "")),
                "from_owner_userid_ref": _safe_owner_ref(source_owner_userid),
                "from_owner_userid_hash": source_owner_hash,
                "from_owner_present": bool(source_owner_userid),
                "to_owner_userid_ref": _safe_owner_ref(target_owner_userid),
                "to_owner_userid_hash": target_owner_hash,
                "to_owner_present": bool(target_owner_userid),
                "operator": operator,
                "source_route": source_route,
                "command_id": source_command_id,
                "trace_id": trace_id,
                "customer_count": customer_count,
                "success_count": success_count,
                "failed_count": failed_count,
                "skipped_count": skipped_count,
                "count_consistency": count_consistency,
                "count_source": count_source,
                "count_source_detail": {
                    "customer_count_source": _text(count_source.get("customer_count")),
                    "success_count_source": _text(count_source.get("success_count")),
                    "failed_count_source": _text(count_source.get("failed_count")),
                    "explicit_success_source": _text(count_source.get("success_count")) not in {"", "none", "customer_count_minus_failed_count", "no_explicit_success_with_failures", "result.touched_count", "touched_customer_scope", "row_customer_scope", "customer_count"},
                },
                "partial_failure_present": partial_failure_present,
                "all_failed": all_failed,
                "customer_scope_present": bool(customer_count),
                "customer_scope_hash": customer_scope_hash,
                "dry_run": False,
                "real_execution": bool(transfer.get("enabled")),
                "created_at": _text(result.get("created_at")),
                "executed_at": _text(result.get("executed_at")),
            },
            "source": {
                "source_module": "owner_migration.application",
                "source_route": source_route,
                "command_id": source_command_id,
                "trace_id": trace_id,
            },
        },
        payload_summary={
            "migration_id": result_id,
            "batch_id": result_id,
            "from_owner_present": bool(source_owner_userid),
            "from_owner_hash": source_owner_hash,
            "to_owner_present": bool(target_owner_userid),
            "to_owner_hash": target_owner_hash,
            "operator": operator,
            "customer_count": customer_count,
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "count_consistency": count_consistency,
            "count_source": count_source,
            "partial_failure_present": partial_failure_present,
            "all_failed": all_failed,
            "source": "owner_migration",
            "executed": True,
        },
    )
    return {"status": "emitted", "event_id": result_payload["event"]["event_id"], "consumer_run_count": len(result_payload.get("consumer_runs") or [])}


def safe_emit(label: str, func, **kwargs: Any) -> dict[str, Any]:
    """Best-effort shadow telemetry only; business events must use transactional outbox."""

    try:
        return func(**kwargs)
    except Exception as exc:
        safe_log_exception(LOGGER, "internal_event_shadow_emit_failed", exc, label=label)
        return {"status": "failed", "error": "internal_event_shadow_emit_failed"}
