from __future__ import annotations

from typing import Any

from aicrm_next.identity_composition import build_identity_write_port
from aicrm_next.crm.identity_contact.application import ListExternalContactOwnerCandidatesQuery
from aicrm_next.crm.identity_contact.dto import BindMobileToExternalContactRequest
from aicrm_next.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform_foundation.command_bus import Command, CommandBus, CommandContext, CommandResult
from aicrm_next.platform_foundation.internal_events.customer_identity import emit_customer_phone_bound_event
from aicrm_next.platform_foundation.internal_events.shadow import safe_emit
from aicrm_next.platform_foundation.side_effects import InMemorySideEffectPlanRepository, SideEffectPlan
from aicrm_next.shared.runtime import production_data_ready

from .commands import (
    BindMobileCommand,
    MarkEnrolledCommand,
    MarkSignupTagCommand,
    PlanMaterialSendCommand,
    SetFollowupSegmentCommand,
    SidebarWriteCommand,
    UnmarkEnrolledCommand,
    UpdateSidebarProfileCommand,
    UpsertLeadPoolClassTermCommand,
)
from .repo import PostgresSidebarWriteRepository, SidebarWriteRepository


class SidebarWriteInputError(ValueError):
    pass


class SidebarWriteNotFoundError(LookupError):
    pass


class SidebarWriteConflictError(RuntimeError):
    pass


class SidebarWriteForbiddenError(PermissionError):
    pass


class SidebarWriteProductionUnavailableError(RuntimeError):
    pass


_repo = SidebarWriteRepository()
_audit_ledger = InMemoryAuditLedger()
_side_effect_plans = InMemorySideEffectPlanRepository()


_PRODUCTION_READY_COMMANDS = (
    BindMobileCommand,
    UpdateSidebarProfileCommand,
    PlanMaterialSendCommand,
)


def _postgres_sidebar_write_repository() -> PostgresSidebarWriteRepository:
    return PostgresSidebarWriteRepository(identity_write_port=build_identity_write_port())


def _audit_hook(command: Command, result: CommandResult) -> None:
    _audit_ledger.record_event(
        event_type=f"{command.command_name}.{result.status}",
        actor_id=result.actor_id,
        actor_type=result.actor_type,
        target_type="sidebar_write",
        target_id=str(command.payload.get("external_userid") or ""),
        source_route=result.source_route,
        command_id=result.command_id,
        trace_id=result.trace_id,
        payload={
            "status": result.status,
            "write_model_status": result.payload.get("write_model_status") or "",
            "real_external_call_executed": False,
        },
    )


_command_bus = CommandBus(audit_hook=_audit_hook)


def reset_sidebar_write_fixture_state() -> None:
    global _repo, _audit_ledger, _side_effect_plans, _command_bus
    _repo = SidebarWriteRepository()
    _audit_ledger = InMemoryAuditLedger()
    _side_effect_plans = InMemorySideEffectPlanRepository()
    _command_bus = CommandBus(audit_hook=_audit_hook)
    _register_handlers()


def get_sidebar_write_audit_events() -> list[dict[str, Any]]:
    return [event.to_dict() for event in _audit_ledger.list_events()]


def get_sidebar_write_side_effect_plans() -> list[dict[str, Any]]:
    return [_plan_response(plan) for plan in _side_effect_plans.list_plans()]


def get_sidebar_write_projection_events() -> list[dict[str, Any]]:
    return [item.to_dict() for item in _repo.list_writes()]


def execute_sidebar_write(command: SidebarWriteCommand) -> dict[str, Any]:
    _validate_command(command)
    _validate_owner_scope(command)
    if production_data_ready() and not isinstance(command, _PRODUCTION_READY_COMMANDS):
        raise SidebarWriteProductionUnavailableError("sidebar write model is not production-ready for command execution")
    platform_command = Command(
        command_name=command.command_name,
        payload=command.to_payload(),
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        context=CommandContext(
            actor_id=command.actor_id,
            actor_type=command.actor_type,
            trace_id=command.trace_id,
            source_route=command.source_route,
            dry_run=command.dry_run,
        ),
    )
    result = _command_bus.execute(platform_command)
    if result.status == "failed":
        if "not found" in result.error:
            raise SidebarWriteNotFoundError(result.error.strip("'") or "not found")
        if "already bound" in result.error:
            raise SidebarWriteConflictError(result.error)
        if "is required" in result.error or "must be" in result.error:
            raise SidebarWriteInputError(result.error)
        raise SidebarWriteProductionUnavailableError(result.error)
    payload = dict(result.payload)
    payload.setdefault("write_model_status", "updated")
    return _response_from_result(result, payload)


def _register_handlers() -> None:
    _command_bus.register(BindMobileCommand.command_name, _handle_bind_mobile)
    _command_bus.register(UpsertLeadPoolClassTermCommand.command_name, _handle_upsert_lead_pool)
    _command_bus.register(MarkSignupTagCommand.command_name, _handle_mark_signup_tag)
    _command_bus.register(SetFollowupSegmentCommand.command_name, _handle_set_followup_segment)
    _command_bus.register(MarkEnrolledCommand.command_name, _handle_mark_enrolled)
    _command_bus.register(UnmarkEnrolledCommand.command_name, _handle_unmark_enrolled)
    _command_bus.register(UpdateSidebarProfileCommand.command_name, _handle_update_profile)
    _command_bus.register(PlanMaterialSendCommand.command_name, _handle_plan_material_send)


def _validate_command(command: SidebarWriteCommand) -> None:
    if not command.external_userid.strip():
        raise SidebarWriteInputError("external_userid is required")
    if not command.source_route.strip():
        raise SidebarWriteInputError("source_route is required")
    if not command.actor_id.strip():
        raise SidebarWriteForbiddenError("sidebar actor is required")


def _validate_owner_scope(command: SidebarWriteCommand) -> None:
    owner_userid = str(command.payload.get("owner_userid") or "").strip()
    if not owner_userid or owner_userid != str(command.actor_id or "").strip():
        raise SidebarWriteForbiddenError("sidebar owner scope forbidden")
    try:
        candidates = ListExternalContactOwnerCandidatesQuery()(external_userid=command.external_userid)
    except Exception as exc:
        raise SidebarWriteProductionUnavailableError("sidebar owner scope unavailable") from exc
    if owner_userid not in candidates:
        raise SidebarWriteForbiddenError("sidebar owner scope forbidden")


def _handle_bind_mobile(command: Command) -> dict[str, Any]:
    payload = dict(command.payload.get("payload") or {})
    mobile = str(payload.get("mobile") or "").strip()
    if not mobile:
        raise SidebarWriteInputError("mobile is required")
    if production_data_ready():
        write = _postgres_sidebar_write_repository().bind_mobile(
            command_id=command.command_id,
            external_userid=str(command.payload["external_userid"]),
            mobile=mobile,
            owner_userid=str(payload.get("owner_userid") or ""),
            bind_by_userid=str(payload.get("bind_by_userid") or command.context.actor_id or ""),
            force_rebind=_as_bool(payload.get("force_rebind"), default=False),
        )
    else:
        write = _repo.bind_mobile(command_id=command.command_id, external_userid=str(command.payload["external_userid"]), mobile=mobile)
    response: dict[str, Any] = {"write_model_status": str(write.get("write_model_status") or "updated"), "write": write}
    if write.get("binding"):
        response["binding"] = write.get("binding")
    elif isinstance(write.get("changes"), dict) and isinstance(write["changes"].get("binding"), dict):
        response["binding"] = write["changes"]["binding"]
    if write.get("lead_pool_merge"):
        response["lead_pool_merge"] = write.get("lead_pool_merge")
    internal_event = _emit_customer_phone_bound_from_sidebar_bind(
        command=command,
        payload=payload,
        mobile=mobile,
        write=write,
    )
    response.update(_internal_event_response(internal_event))
    return response


def _emit_customer_phone_bound_from_sidebar_bind(
    *,
    command: Command,
    payload: dict[str, Any],
    mobile: str,
    write: dict[str, Any],
) -> dict[str, Any]:
    binding = _sidebar_binding_payload(write)
    binding_status = _sidebar_binding_status(binding)
    if not binding:
        return {"status": "skipped", "reason": "sidebar_binding_payload_missing"}
    request = BindMobileToExternalContactRequest(
        external_userid=str(command.payload["external_userid"]),
        mobile=mobile,
        owner_userid=str(payload.get("owner_userid") or binding.get("owner_userid") or ""),
        bind_by_userid=str(payload.get("bind_by_userid") or command.context.actor_id or ""),
        customer_name=str(payload.get("customer_name") or binding.get("customer_name") or ""),
        force_rebind=_as_bool(payload.get("force_rebind"), default=False),
    )
    binding_result = {
        "ok": True,
        "binding_status": binding_status,
        "unionid": str(binding.get("unionid") or write.get("unionid") or ""),
        "external_userid": str(binding.get("external_userid") or command.payload["external_userid"]),
        "mobile": str(binding.get("mobile") or mobile),
        "owner_userid": str(binding.get("owner_userid") or payload.get("owner_userid") or ""),
        "follow_user_userid": str(binding.get("follow_user_userid") or binding.get("owner_userid") or payload.get("owner_userid") or ""),
        "matched_by": str(binding.get("matched_by") or "sidebar_bind_mobile"),
        "source_status": str(write.get("write_type") or write.get("write_model_status") or "sidebar_bind_mobile"),
    }
    return safe_emit(
        "customer.phone_bound",
        emit_customer_phone_bound_event,
        request=request,
        binding_result=binding_result,
        source_module="sidebar_write.application",
        source_route=command.context.source_route or "/api/sidebar/bind-mobile",
    )


def _sidebar_binding_payload(write: dict[str, Any]) -> dict[str, Any]:
    binding = write.get("binding")
    if isinstance(binding, dict):
        return dict(binding)
    changes = write.get("changes")
    if isinstance(changes, dict) and isinstance(changes.get("binding"), dict):
        return dict(changes["binding"])
    return {}


def _sidebar_binding_status(binding: dict[str, Any]) -> str:
    status = str(binding.get("binding_status") or "").strip()
    if status:
        return status
    if binding.get("is_bound") is True:
        return "bound"
    return ""


def _internal_event_response(internal_event: dict[str, Any]) -> dict[str, Any]:
    return {
        "internal_event_status": str(internal_event.get("status") or ""),
        "internal_event_id": str(internal_event.get("event_id") or ""),
        "internal_event_reason": str(internal_event.get("reason") or ""),
        "internal_event_error": str(internal_event.get("error") or ""),
        "internal_event_consumer_run_count": int(internal_event.get("consumer_run_count") or 0),
    }


def _handle_upsert_lead_pool(command: Command) -> dict[str, Any]:
    payload = dict(command.payload.get("payload") or {})
    class_term = str(payload.get("class_term") or payload.get("class_term_no") or "").strip()
    status = str(payload.get("status") or payload.get("lead_pool_status") or "").strip()
    if not class_term:
        raise SidebarWriteInputError("class_term is required")
    if not status:
        raise SidebarWriteInputError("status is required")
    write = _repo.upsert_lead_pool_class_term(
        command_id=command.command_id,
        external_userid=str(command.payload["external_userid"]),
        class_term=class_term,
        status=status,
    )
    return {"write_model_status": "updated", "write": write}


def _handle_mark_signup_tag(command: Command) -> dict[str, Any]:
    payload = dict(command.payload.get("payload") or {})
    tag_name = str(payload.get("tag_name") or "").strip()
    tag_id = str(payload.get("tag_id") or "").strip()
    if not tag_name and not tag_id:
        raise SidebarWriteInputError("tag_name or tag_id is required")
    marked = _as_bool(payload.get("marked"), default=True)
    write = _repo.mark_signup_tag(
        command_id=command.command_id,
        external_userid=str(command.payload["external_userid"]),
        tag_id=tag_id,
        tag_name=tag_name or tag_id,
        marked=marked,
        source=str(payload.get("source") or "sidebar"),
    )
    plan = _create_side_effect_plan(
        command=command,
        effect_type="wecom.tag.update",
        adapter_name="wecom",
        target_type="external_user",
        target_id=str(command.payload["external_userid"]),
        payload_summary={"tag_id": tag_id, "tag_name": tag_name, "marked": marked},
        risk_level="medium",
    )
    return {"write_model_status": "updated", "write": write, "side_effect_plan": _plan_response(plan)}


def _handle_set_followup_segment(command: Command) -> dict[str, Any]:
    segment = str(command.payload.get("payload", {}).get("segment") or "").strip()
    if not segment:
        raise SidebarWriteInputError("segment is required")
    write = _repo.set_followup_segment(
        command_id=command.command_id,
        external_userid=str(command.payload["external_userid"]),
        segment=segment,
    )
    plan = _create_side_effect_plan(
        command=command,
        effect_type="automation.followup_segment_changed",
        adapter_name="automation_runtime",
        target_type="external_user",
        target_id=str(command.payload["external_userid"]),
        payload_summary={"segment": segment},
        risk_level="medium",
    )
    return {"write_model_status": "updated", "write": write, "side_effect_plan": _plan_response(plan)}


def _handle_mark_enrolled(command: Command) -> dict[str, Any]:
    write = _repo.mark_enrolled(command_id=command.command_id, external_userid=str(command.payload["external_userid"]), enrolled=True)
    return {"write_model_status": "updated", "write": write}


def _handle_unmark_enrolled(command: Command) -> dict[str, Any]:
    write = _repo.mark_enrolled(command_id=command.command_id, external_userid=str(command.payload["external_userid"]), enrolled=False)
    return {"write_model_status": "updated", "write": write}


def _handle_update_profile(command: Command) -> dict[str, Any]:
    payload = dict(command.payload.get("payload") or {})
    remark = str(payload.get("remark") or "").strip()
    description = str(payload.get("description") or "").strip()
    display_name = str(payload.get("display_name") or payload.get("customer_name") or "").strip()
    profile_field_keys = {"source", "industry", "industry_description", "needs_blockers_followup"}
    has_profile_fields = any(key in payload for key in profile_field_keys)
    if not has_profile_fields and not any([remark, description, display_name]):
        raise SidebarWriteInputError("profile field, remark, description, or display_name is required")
    repository = _postgres_sidebar_write_repository() if production_data_ready() else _repo
    write = repository.update_profile(
        command_id=command.command_id,
        external_userid=str(command.payload["external_userid"]),
        remark=remark,
        description=description,
        display_name=display_name,
        source=str(payload.get("source") or ""),
        industry=str(payload.get("industry") or ""),
        industry_description=str(payload.get("industry_description") or ""),
        needs_blockers_followup=str(payload.get("needs_blockers_followup") or ""),
        updated_by=str(payload.get("updated_by") or payload.get("operator") or command.context.actor_id or ""),
        owner_userid=str(payload.get("owner_userid") or ""),
        profile_fields_present=has_profile_fields,
    )
    plan = _create_side_effect_plan(
        command=command,
        effect_type="wecom.profile.update",
        adapter_name="wecom",
        target_type="external_user",
        target_id=str(command.payload["external_userid"]),
        payload_summary={
            key: value
            for key, value in {
                "remark": remark,
                "description": description,
                "display_name": display_name,
                "source": str(payload.get("source") or ""),
                "industry": str(payload.get("industry") or ""),
                "industry_description": str(payload.get("industry_description") or ""),
                "needs_blockers_followup": str(payload.get("needs_blockers_followup") or ""),
            }.items()
            if value
        },
        risk_level="medium",
    )
    return {"write_model_status": "updated", "write": write, "side_effect_plan": _plan_response(plan)}


def _handle_plan_material_send(command: Command) -> dict[str, Any]:
    payload = dict(command.payload.get("payload") or {})
    material_id = str(payload.get("material_id") or "").strip()
    if not material_id:
        raise SidebarWriteInputError("material_id is required")
    repository = _postgres_sidebar_write_repository() if production_data_ready() else _repo
    write = repository.record_material_send_plan(
        command_id=command.command_id,
        external_userid=str(command.payload["external_userid"]),
        material_id=material_id,
        material_type=str(payload.get("type") or payload.get("material_type") or ""),
        operator=str(payload.get("operator") or command.context.actor_id or ""),
        delivery_mode=str(payload.get("delivery_mode") or ""),
        owner_userid=str(payload.get("owner_userid") or ""),
    )
    plan = _create_side_effect_plan(
        command=command,
        effect_type="wecom.material.send",
        adapter_name="wecom",
        target_type="external_user",
        target_id=str(command.payload["external_userid"]),
        payload_summary={
            "material_id": material_id,
            "type": str(payload.get("type") or payload.get("material_type") or ""),
            "delivery_mode": str(payload.get("delivery_mode") or ""),
        },
        risk_level="high",
    )
    response = {"write_model_status": "planned", "write": write, "side_effect_plan": _plan_response(plan)}
    if write.get("media_id"):
        response["media_id"] = write.get("media_id")
    return response


def _create_side_effect_plan(
    *,
    command: Command,
    effect_type: str,
    adapter_name: str,
    target_type: str,
    target_id: str,
    payload_summary: dict[str, Any],
    risk_level: str,
) -> SideEffectPlan:
    return _side_effect_plans.create_plan(
        command_id=command.command_id,
        effect_type=effect_type,
        adapter_name=adapter_name,
        adapter_mode="real_blocked",
        target_type=target_type,
        target_id=target_id,
        payload={
            "payload_summary": payload_summary,
            "real_external_call_executed": False,
        },
        status="planned",
        risk_level=risk_level,
        requires_approval=True,
    )


def _plan_response(plan: SideEffectPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    summary = dict(payload.pop("payload") or {})
    payload["payload_summary"] = summary.get("payload_summary") or {}
    payload["real_external_call_executed"] = False
    return payload


def _response_from_result(result: CommandResult, payload: dict[str, Any]) -> dict[str, Any]:
    response = {
        "ok": result.status in {"completed", "dry_run"},
        "command_id": result.command_id,
        "command_name": result.command_name,
        "idempotency_key": result.idempotency_key,
        "source_status": "next_command",
        "write_model_status": payload.get("write_model_status") or "updated",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
        "audit_recorded": True,
        "command_result_status": result.status,
    }
    response.update(payload)
    if "side_effect_plan" in response:
        response.setdefault("source_status", "next_command")
    return response


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "y"}:
        return True
    if normalized in {"0", "false", "no", "off", "n"}:
        return False
    return default


_register_handlers()
