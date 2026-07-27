from __future__ import annotations

from typing import Any

from aicrm_next.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform_foundation.command_bus import Command, CommandBus, CommandContext, CommandResult
from aicrm_next.platform_foundation.external_effects import ExternalEffectService, WECOM_CONTACT_TAG_MARK, WECOM_CONTACT_TAG_UNMARK
from aicrm_next.platform_foundation.internal_events.legacy_path_markers import mark_legacy_path_invoked
from aicrm_next.platform_foundation.internal_events.shadow import emit_customer_tag_shadow_event, safe_emit
from aicrm_next.platform_foundation.side_effects import InMemorySideEffectPlanRepository, SideEffectPlan

from .mutation_commands import (
    PlanCustomerTagAssignmentCommand,
    PlanQuestionnaireTagSideEffectCommand,
    PlanWeComTagMarkCommand,
    PlanWeComTagUnmarkCommand,
    WeComTagMutationCommand,
)


class WeComTagMutationInputError(ValueError):
    pass


_audit_ledger = InMemoryAuditLedger()
_side_effect_plans = InMemorySideEffectPlanRepository()
_command_bus = CommandBus()


def _audit_hook(command: Command, result: CommandResult) -> None:
    _audit_ledger.record_event(
        event_type=f"{command.command_name}.{result.status}",
        actor_id=result.actor_id,
        actor_type=result.actor_type,
        target_type="external_user",
        target_id=str(command.payload.get("external_userid") or ""),
        source_route=result.source_route,
        command_id=result.command_id,
        trace_id=result.trace_id,
        payload={
            "status": result.status,
            "source_status": "next_command",
            "fallback_used": False,
            "adapter_mode": "queued_external_effect",
            "real_external_call_executed": False,
            "wecom_api_called": False,
        },
    )


def reset_wecom_tag_live_mutation_fixture_state() -> None:
    global _audit_ledger, _side_effect_plans, _command_bus
    _audit_ledger = InMemoryAuditLedger()
    _side_effect_plans = InMemorySideEffectPlanRepository()
    _command_bus = CommandBus(audit_hook=_audit_hook)
    _register_handlers()


def get_wecom_tag_live_mutation_audit_events() -> list[dict[str, Any]]:
    return [event.to_dict() for event in _audit_ledger.list_events()]


def get_wecom_tag_live_mutation_side_effect_plans() -> list[dict[str, Any]]:
    return [_plan_response(plan) for plan in _side_effect_plans.list_plans()]


def tag_execution_status() -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": "tag_execution_status",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "adapter_name": "wecom",
        "adapter_mode": "local_projection_and_external_effect",
        "mode": "local_projection_and_external_effect",
        "local_projection_supported": True,
        "external_effect_supported": True,
        "worker_required": True,
        "real_enabled": True,
        "available": True,
        "blocked": False,
        "requires_approval": False,
        "blocking_reason": "",
        "real_external_call_executed": False,
        "wecom_api_called": False,
        "live_call_executed": False,
        "mark_tag_executed": False,
        "unmark_tag_executed": False,
    }


def live_gate_status() -> dict[str, Any]:
    """Deprecated compatibility alias; use tag_execution_status()."""
    return tag_execution_status()


def execute_wecom_tag_mutation(command: WeComTagMutationCommand) -> dict[str, Any]:
    normalized = _normalize_command(command)
    platform_command = Command(
        command_name=normalized.command_name,
        payload=normalized.to_payload(),
        command_id=normalized.command_id,
        idempotency_key=normalized.idempotency_key,
        context=CommandContext(
            actor_id=normalized.actor_id,
            actor_type=normalized.actor_type,
            trace_id=normalized.trace_id,
            source_route=normalized.source_route,
            dry_run=normalized.dry_run,
        ),
    )
    result = _command_bus.execute(platform_command)
    if result.status == "failed":
        raise WeComTagMutationInputError(result.error)
    payload = dict(result.payload)
    return _response_from_result(result, payload)


def _register_handlers() -> None:
    for command_type in (
        PlanWeComTagMarkCommand,
        PlanWeComTagUnmarkCommand,
        PlanCustomerTagAssignmentCommand,
        PlanQuestionnaireTagSideEffectCommand,
    ):
        _command_bus.register(command_type.command_name, _handle_plan_mutation)


def _normalize_command(command: WeComTagMutationCommand) -> WeComTagMutationCommand:
    external_userid = str(command.external_userid or "").strip()
    tag_ids = _normalize_tag_ids(command.tag_ids)
    if not external_userid:
        raise WeComTagMutationInputError("external_userid is required")
    if not tag_ids:
        raise WeComTagMutationInputError("tag_ids is required")
    if not str(command.source_route or "").strip():
        raise WeComTagMutationInputError("source_route is required")
    return command.__class__(
        command_id=command.command_id,
        idempotency_key=str(command.idempotency_key or "").strip(),
        actor_id=str(command.actor_id or "wecom_tag_operator").strip(),
        actor_type=str(command.actor_type or "user").strip(),
        external_userid=external_userid,
        tag_ids=tag_ids,
        source_route=str(command.source_route or "").strip(),
        source_context=dict(command.source_context or {}),
        dry_run=bool(command.dry_run),
        trace_id=str(command.trace_id or "").strip(),
    )


def _handle_plan_mutation(command: Command) -> dict[str, Any]:
    effect_type = command.command_name
    external_userid = str(command.payload.get("external_userid") or "").strip()
    tag_ids = list(command.payload.get("tag_ids") or [])
    mark_legacy_path_invoked(
        legacy_path="customer_tag.legacy_wecom_side_effect_planning",
        replacement_event_type="customer.untagged" if effect_type == "wecom.tag.unmark" else "customer.tagged",
        replacement_consumer="tag_external_effect_shadow_consumer",
        source_module="customer_tags.live_mutation",
        source_route="execute_wecom_tag_mutation",
        aggregate_id=command.idempotency_key or command.command_id,
        reason="customer_tag_wecom_side_effect_replaced_by_internal_event_consumer",
    )
    plan = _create_side_effect_plan(
        command=command,
        effect_type=effect_type,
        external_userid=external_userid,
        tag_ids=tag_ids,
        source_context=dict(command.payload.get("source_context") or {}),
    )
    external_effect_job = _plan_wecom_tag_external_effect_job(
        command=command,
        effect_type=effect_type,
        external_userid=external_userid,
        tag_ids=tag_ids,
        source_context=dict(command.payload.get("source_context") or {}),
    )
    internal_event = safe_emit(
        "customer.tag_mutation",
        emit_customer_tag_shadow_event,
        command=command,
        effect_type=effect_type,
        external_userid=external_userid,
        tag_ids=tag_ids,
        source_context=dict(command.payload.get("source_context") or {}),
        side_effect_plan=_plan_response(plan),
        external_effect_job=external_effect_job,
    )
    return {
        "effect_type": effect_type,
        "external_userid": external_userid,
        "tag_ids": tag_ids,
        "source_context": dict(command.payload.get("source_context") or {}),
        "side_effect_plan": _plan_response(plan),
        "external_effect_job": external_effect_job,
        "external_effect_job_id": external_effect_job.get("id") if external_effect_job else None,
        "internal_event_id": internal_event.get("event_id") or "",
        "internal_event_status": internal_event.get("status") or "",
    }


def _create_side_effect_plan(
    *,
    command: Command,
    effect_type: str,
    external_userid: str,
    tag_ids: list[str],
    source_context: dict[str, Any],
) -> SideEffectPlan:
    return _side_effect_plans.create_plan(
        command_id=command.command_id,
        effect_type=effect_type,
        adapter_name="wecom_tag",
        adapter_mode="queued_external_effect",
        target_type="external_user",
        target_id=external_userid,
        payload={
            "payload_summary": {
                "external_userid_redacted": _redact_external_userid(external_userid),
                "tag_count": len(tag_ids),
                "source": source_context.get("source") or command.context.source_route,
            },
            "external_userid": external_userid,
            "tag_ids": tag_ids,
            "source_context": source_context,
            "follow_user_userid": _follow_user_userid(command=command, source_context=source_context),
            "real_external_call_executed": False,
            "wecom_api_called": False,
        },
        status="queued",
        risk_level="high",
        requires_approval=False,
    )


def _plan_wecom_tag_external_effect_job(
    *,
    command: Command,
    effect_type: str,
    external_userid: str,
    tag_ids: list[str],
    source_context: dict[str, Any],
) -> dict[str, Any] | None:
    external_effect_type = WECOM_CONTACT_TAG_UNMARK if effect_type == "wecom.tag.unmark" else WECOM_CONTACT_TAG_MARK
    try:
        return ExternalEffectService().plan_effect(
            effect_type=external_effect_type,
            adapter_name="wecom_tag",
            operation="tag_unmark" if external_effect_type == WECOM_CONTACT_TAG_UNMARK else "tag_mark",
            target_type="external_user",
            target_id=external_userid,
            business_type="wecom_tag",
            business_id=external_userid,
            payload={
                "external_userid": external_userid,
                "tag_ids": tag_ids,
                "follow_user_userid": _follow_user_userid(command=command, source_context=source_context),
                "operator": command.context.actor_id,
                "source_context": source_context,
                "external_effect_queue_required": True,
                "bypass_push_capability": bool(source_context.get("bypass_push_capability")),
            },
            payload_summary={
                "external_userid_redacted": _redact_external_userid(external_userid),
                "tag_count": len(tag_ids),
                "source": source_context.get("source") or command.context.source_route,
                "follow_user_userid_present": bool(_follow_user_userid(command=command, source_context=source_context)),
                "wecom_api_called": False,
            },
            context=command.context,
            source_module="customer_tags.live_mutation",
            source_command_id=command.command_id,
            risk_level="high",
            requires_approval=False,
            execution_mode="execute",
            status="queued",
            idempotency_key=f"{command.idempotency_key or command.command_id}:external-effect:{external_effect_type}",
        )
    except Exception:
        return None


def _plan_response(plan: SideEffectPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    plan_payload = dict(payload.pop("payload") or {})
    payload["payload_summary"] = plan_payload.get("payload_summary") or {}
    payload["external_userid"] = plan_payload.get("external_userid") or ""
    payload["tag_ids"] = list(plan_payload.get("tag_ids") or [])
    payload["source_context"] = dict(plan_payload.get("source_context") or {})
    payload["follow_user_userid"] = plan_payload.get("follow_user_userid") or ""
    payload["real_external_call_executed"] = False
    payload["wecom_api_called"] = False
    return payload


def _response_from_result(result: CommandResult, payload: dict[str, Any]) -> dict[str, Any]:
    effect_type = str(payload.get("effect_type") or result.command_name)
    source_context = dict(payload.get("source_context") or {})
    local_projection = dict(source_context.get("local_projection") or {})
    return {
        "ok": result.status in {"completed", "dry_run"},
        "command_id": result.command_id,
        "command_name": result.command_name,
        "idempotency_key": result.idempotency_key,
        "source_status": "next_command",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "adapter_mode": "queued_external_effect",
        "effect_type": effect_type,
        "external_userid": payload.get("external_userid") or "",
        "tag_ids": list(payload.get("tag_ids") or []),
        "local_projection": local_projection,
        "local_projection_updated": bool(
            source_context.get("local_projection_updated") or local_projection.get("local_projection_updated")
        ),
        "local_projection_status": local_projection.get("local_projection_status") or "",
        "side_effect_plan": payload.get("side_effect_plan") or {},
        "external_effect_job": payload.get("external_effect_job"),
        "external_effect_job_id": payload.get("external_effect_job_id"),
        "external_effect_status": "queued" if payload.get("external_effect_job_id") else "blocked",
        "mark_tag_queued": effect_type != "wecom.tag.unmark" and bool(payload.get("external_effect_job_id")),
        "unmark_tag_queued": effect_type == "wecom.tag.unmark" and bool(payload.get("external_effect_job_id")),
        "internal_event_id": payload.get("internal_event_id") or "",
        "internal_event_status": payload.get("internal_event_status") or "",
        "real_external_call_executed": False,
        "wecom_api_called": False,
        "live_call_executed": False,
        "mark_tag_executed": False,
        "unmark_tag_executed": False,
        "audit_recorded": True,
        "command_result_status": result.status,
    }


def _normalize_tag_ids(tag_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tag_ids or []:
        tag_id = str(raw or "").strip()
        if not tag_id or tag_id in seen:
            continue
        seen.add(tag_id)
        normalized.append(tag_id)
    return normalized


def _redact_external_userid(external_userid: str) -> str:
    value = str(external_userid or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]}"


def _follow_user_userid(*, command: Command, source_context: dict[str, Any]) -> str:
    return str(
        source_context.get("follow_user_userid")
        or source_context.get("owner_userid")
        or source_context.get("operator")
        or command.payload.get("follow_user_userid")
        or ""
    ).strip()


reset_wecom_tag_live_mutation_fixture_state()
