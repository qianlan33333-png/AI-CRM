from __future__ import annotations

from typing import Any

from aicrm_next.platform.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform.platform_foundation.command_bus import Command, CommandBus, CommandContext, CommandResult
from aicrm_next.platform.platform_foundation.side_effects import InMemorySideEffectPlanRepository, SideEffectPlan
from aicrm_next.platform.shared.runtime import production_data_ready, production_environment

from aicrm_next.integration_ports import build_wecom_tag_live_gateway

from .commands import (
    CreateWeComTagCommand,
    CreateWeComTagGroupCommand,
    DeleteWeComTagCommand,
    DeleteWeComTagGroupCommand,
    SyncWeComTagCatalogCommand,
    UpdateWeComTagCommand,
    UpdateWeComTagGroupCommand,
    WeComTagWriteCommand,
)
from .sync_service import execute_wecom_tag_catalog_sync
from .write_repo import PostgresWeComTagWriteRepository, WeComTagWriteRepository


class WeComTagWriteInputError(ValueError):
    pass


class WeComTagWriteNotFoundError(LookupError):
    pass


class WeComTagWriteProductionUnavailableError(RuntimeError):
    pass


_repo = WeComTagWriteRepository()
_audit_ledger = InMemoryAuditLedger()
_side_effect_plans = InMemorySideEffectPlanRepository()


def _audit_hook(command: Command, result: CommandResult) -> None:
    _audit_ledger.record_event(
        event_type=f"{command.command_name}.{result.status}",
        actor_id=result.actor_id,
        actor_type=result.actor_type,
        target_type=_target_type(command.command_name),
        target_id=str(command.payload.get("target_id") or result.payload.get("target_id") or ""),
        source_route=result.source_route,
        command_id=result.command_id,
        trace_id=result.trace_id,
        payload={
            "status": result.status,
            "write_model_status": result.payload.get("write_model_status") or "",
            "fallback_used": False,
            "real_external_call_executed": False,
            "sync_executed": False,
        },
    )


_command_bus = CommandBus(audit_hook=_audit_hook)


def reset_wecom_tag_write_fixture_state() -> None:
    global _repo, _audit_ledger, _side_effect_plans, _command_bus
    _repo = WeComTagWriteRepository()
    _audit_ledger = InMemoryAuditLedger()
    _side_effect_plans = InMemorySideEffectPlanRepository()
    _command_bus = CommandBus(audit_hook=_audit_hook)
    _register_handlers()


def get_wecom_tag_write_audit_events() -> list[dict[str, Any]]:
    return [event.to_dict() for event in _audit_ledger.list_events()]


def get_wecom_tag_write_side_effect_plans() -> list[dict[str, Any]]:
    return [_plan_response(plan) for plan in _side_effect_plans.list_plans()]


def get_wecom_tag_write_projection_events() -> list[dict[str, Any]]:
    return _repo.list_writes()


def execute_wecom_tag_write(command: WeComTagWriteCommand) -> dict[str, Any]:
    _validate_command(command)
    if production_environment() and not production_data_ready():
        raise WeComTagWriteProductionUnavailableError("wecom tag write model is not production-ready for command execution")

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
            raise WeComTagWriteNotFoundError(result.error)
        if "is required" in result.error or "already exists" in result.error:
            raise WeComTagWriteInputError(result.error)
        raise WeComTagWriteProductionUnavailableError(result.error)

    payload = dict(result.payload)
    payload.setdefault("write_model_status", "local_projection_updated")
    return _response_from_result(result, payload)


def _register_handlers() -> None:
    _command_bus.register(CreateWeComTagCommand.command_name, _handle_create_tag)
    _command_bus.register(UpdateWeComTagCommand.command_name, _handle_update_tag)
    _command_bus.register(DeleteWeComTagCommand.command_name, _handle_delete_tag)
    _command_bus.register(CreateWeComTagGroupCommand.command_name, _handle_create_group)
    _command_bus.register(UpdateWeComTagGroupCommand.command_name, _handle_update_group)
    _command_bus.register(DeleteWeComTagGroupCommand.command_name, _handle_delete_group)
    _command_bus.register(SyncWeComTagCatalogCommand.command_name, _handle_sync_catalog)


def _validate_command(command: WeComTagWriteCommand) -> None:
    if not command.command_id.strip():
        raise WeComTagWriteInputError("command_id is required")
    if not command.source_route.strip():
        raise WeComTagWriteInputError("source_route is required")
    if not command.actor_id.strip():
        raise WeComTagWriteInputError("actor_id is required")
    if command.command_name in {
        UpdateWeComTagCommand.command_name,
        DeleteWeComTagCommand.command_name,
        UpdateWeComTagGroupCommand.command_name,
        DeleteWeComTagGroupCommand.command_name,
    } and not command.target_id.strip():
        raise WeComTagWriteInputError("target_id is required")


def _handle_create_tag(command: Command) -> dict[str, Any]:
    requested = dict(command.payload.get("payload") or {})
    if not str(requested.get("group_id") or "").strip():
        raise ValueError("group_id is required")
    if production_data_ready():
        return _handle_live_create_tag(command, requested)
    tag = _write_repository().create_tag(
        command_id=command.command_id,
        group_id=str(requested.get("group_id") or "").strip(),
        tag_name=str(requested.get("tag_name") or "").strip(),
    )
    plan = _create_side_effect_plan(command=command, effect_type="wecom.tag.create", target_type="wecom_tag", target_id=tag["tag_id"], payload_summary={"group_id": tag["group_id"], "tag_name": tag["tag_name"]}, risk_level="medium")
    return {"target_id": tag["tag_id"], "tag": tag, "write_model_status": "local_projection_updated", "local_projection_updated": True, "side_effect_plan": _plan_response(plan)}


def _handle_update_tag(command: Command) -> dict[str, Any]:
    requested = dict(command.payload.get("payload") or {})
    if production_data_ready():
        return _handle_live_update_tag(command, requested)
    tag = _write_repository().update_tag(command_id=command.command_id, tag_id=str(command.payload.get("target_id") or ""), tag_name=str(requested.get("tag_name") or "").strip())
    plan = _create_side_effect_plan(command=command, effect_type="wecom.tag.update", target_type="wecom_tag", target_id=tag["tag_id"], payload_summary={"tag_id": tag["tag_id"], "tag_name": tag["tag_name"]}, risk_level="medium")
    return {"target_id": tag["tag_id"], "tag": tag, "write_model_status": "local_projection_updated", "local_projection_updated": True, "side_effect_plan": _plan_response(plan)}


def _handle_delete_tag(command: Command) -> dict[str, Any]:
    if production_data_ready():
        return _handle_live_delete_tag(command)
    tag = _write_repository().delete_tag(command_id=command.command_id, tag_id=str(command.payload.get("target_id") or ""))
    plan = _create_side_effect_plan(command=command, effect_type="wecom.tag.delete", target_type="wecom_tag", target_id=tag["tag_id"], payload_summary={"tag_id": tag["tag_id"], "group_id": tag["group_id"]}, risk_level="high")
    return {"target_id": tag["tag_id"], "tag": tag, "deleted": True, "write_model_status": "local_projection_updated", "local_projection_updated": True, "side_effect_plan": _plan_response(plan)}


def _handle_create_group(command: Command) -> dict[str, Any]:
    requested = dict(command.payload.get("payload") or {})
    if production_data_ready():
        return _handle_live_create_group(command, requested)
    result = _write_repository().create_group(
        command_id=command.command_id,
        group_name=str(requested.get("group_name") or "").strip(),
        first_tag_name=str(requested.get("first_tag_name") or "").strip(),
    )
    group = result["group"]
    plan = _create_side_effect_plan(command=command, effect_type="wecom.tag_group.create", target_type="wecom_tag_group", target_id=group["group_id"], payload_summary={"group_name": group["group_name"], "first_tag_requested": bool(requested.get("first_tag_name"))}, risk_level="medium")
    return {"target_id": group["group_id"], "group": group, "tags": result.get("tags") or [], "write_model_status": "local_projection_updated", "local_projection_updated": True, "side_effect_plan": _plan_response(plan)}


def _handle_update_group(command: Command) -> dict[str, Any]:
    requested = dict(command.payload.get("payload") or {})
    if production_data_ready():
        return _handle_live_update_group(command, requested)
    group = _write_repository().update_group(command_id=command.command_id, group_id=str(command.payload.get("target_id") or ""), group_name=str(requested.get("group_name") or "").strip())
    plan = _create_side_effect_plan(command=command, effect_type="wecom.tag_group.update", target_type="wecom_tag_group", target_id=group["group_id"], payload_summary={"group_id": group["group_id"], "group_name": group["group_name"]}, risk_level="medium")
    return {"target_id": group["group_id"], "group": group, "write_model_status": "local_projection_updated", "local_projection_updated": True, "side_effect_plan": _plan_response(plan)}


def _handle_delete_group(command: Command) -> dict[str, Any]:
    if production_data_ready():
        return _handle_live_delete_group(command)
    result = _write_repository().delete_group(command_id=command.command_id, group_id=str(command.payload.get("target_id") or ""))
    group = result["group"]
    plan = _create_side_effect_plan(command=command, effect_type="wecom.tag_group.delete", target_type="wecom_tag_group", target_id=group["group_id"], payload_summary={"group_id": group["group_id"], "deleted_tag_count": len(result.get("deleted_tag_ids") or [])}, risk_level="high")
    return {"target_id": group["group_id"], "group": group, "deleted": True, "deleted_tag_ids": result.get("deleted_tag_ids") or [], "write_model_status": "local_projection_updated", "local_projection_updated": True, "side_effect_plan": _plan_response(plan)}


def _handle_sync_catalog(command: Command) -> dict[str, Any]:
    sync = _write_repository().sync_catalog(command_id=command.command_id)
    plan = _create_side_effect_plan(command=command, effect_type="wecom.tag.sync", target_type="wecom_tag_catalog", target_id="catalog", payload_summary={"operation": "sync_catalog", "local_only": True}, risk_level="high")
    return {"target_id": "catalog", "sync": sync, "write_model_status": "local_projection_updated", "local_projection_updated": True, "sync_executed": False, "side_effect_plan": _plan_response(plan)}


def _write_repository() -> WeComTagWriteRepository | PostgresWeComTagWriteRepository:
    if production_data_ready():
        return PostgresWeComTagWriteRepository()
    return _repo


def _live_gateway():
    return build_wecom_tag_live_gateway()


def _sync_live_projection(*, command: Command, gateway) -> dict[str, Any]:
    return execute_wecom_tag_catalog_sync(operator=str(command.context.actor_id or "wecom_tag_admin"), gateway=gateway)


def _handle_live_create_tag(command: Command, requested: dict[str, Any]) -> dict[str, Any]:
    group_id = str(requested.get("group_id") or "").strip()
    tag_name = str(requested.get("tag_name") or "").strip()
    if not tag_name:
        raise ValueError("tag_name is required")
    gateway = _live_gateway()
    live_result = gateway.add_corp_tag_live(group_id=group_id, tags=[{"name": tag_name}])
    sync = _sync_live_projection(command=command, gateway=gateway)
    tag = _tag_from_add_result(live_result, group_id=group_id, tag_name=tag_name)
    plan = _create_live_side_effect_plan(
        command=command,
        effect_type="wecom.tag.create",
        target_type="wecom_tag",
        target_id=tag["tag_id"],
        payload_summary={"group_id": group_id, "tag_name": tag_name},
    )
    return _live_write_response(target_id=tag["tag_id"], side_effect_plan=plan, sync=sync, tag=tag)


def _handle_live_update_tag(command: Command, requested: dict[str, Any]) -> dict[str, Any]:
    tag_id = str(command.payload.get("target_id") or "").strip()
    tag_name = str(requested.get("tag_name") or "").strip()
    if not tag_id:
        raise ValueError("tag_id is required")
    if not tag_name:
        raise ValueError("tag_name is required")
    before = _write_repository().get_tag(tag_id) or {"tag_id": tag_id, "group_id": ""}
    gateway = _live_gateway()
    gateway.edit_corp_tag_live(tag_or_group_id=tag_id, name=tag_name)
    sync = _sync_live_projection(command=command, gateway=gateway)
    tag = {**before, "tag_name": tag_name, "source": "live_wecom_tag_write"}
    plan = _create_live_side_effect_plan(
        command=command,
        effect_type="wecom.tag.update",
        target_type="wecom_tag",
        target_id=tag_id,
        payload_summary={"tag_id": tag_id, "tag_name": tag_name},
    )
    return _live_write_response(target_id=tag_id, side_effect_plan=plan, sync=sync, tag=tag)


def _handle_live_delete_tag(command: Command) -> dict[str, Any]:
    tag_id = str(command.payload.get("target_id") or "").strip()
    if not tag_id:
        raise ValueError("tag_id is required")
    tag = _write_repository().get_tag(tag_id) or {"tag_id": tag_id, "group_id": ""}
    gateway = _live_gateway()
    gateway.delete_corp_tag_live(tag_ids=[tag_id])
    sync = _sync_live_projection(command=command, gateway=gateway)
    plan = _create_live_side_effect_plan(
        command=command,
        effect_type="wecom.tag.delete",
        target_type="wecom_tag",
        target_id=tag_id,
        payload_summary={"tag_id": tag_id, "group_id": tag.get("group_id") or ""},
        risk_level="high",
    )
    return _live_write_response(target_id=tag_id, side_effect_plan=plan, sync=sync, tag=tag, deleted=True)


def _handle_live_create_group(command: Command, requested: dict[str, Any]) -> dict[str, Any]:
    group_name = str(requested.get("group_name") or "").strip()
    first_tag_name = str(requested.get("first_tag_name") or "").strip()
    if not group_name:
        raise ValueError("group_name is required")
    if not first_tag_name:
        raise ValueError("first_tag_name is required for live WeCom tag-group creation")
    gateway = _live_gateway()
    live_result = gateway.add_corp_tag_live(group_name=group_name, tags=[{"name": first_tag_name}])
    sync = _sync_live_projection(command=command, gateway=gateway)
    group = _group_from_add_result(live_result, group_name=group_name)
    tag = _tag_from_add_result(live_result, group_id=group["group_id"], tag_name=first_tag_name)
    plan = _create_live_side_effect_plan(
        command=command,
        effect_type="wecom.tag_group.create",
        target_type="wecom_tag_group",
        target_id=group["group_id"],
        payload_summary={"group_name": group_name, "first_tag_requested": True},
    )
    return _live_write_response(target_id=group["group_id"], side_effect_plan=plan, sync=sync, group=group, tags=[tag])


def _handle_live_update_group(command: Command, requested: dict[str, Any]) -> dict[str, Any]:
    group_id = str(command.payload.get("target_id") or "").strip()
    group_name = str(requested.get("group_name") or "").strip()
    if not group_id:
        raise ValueError("group_id is required")
    if not group_name:
        raise ValueError("group_name is required")
    before = _write_repository().get_group(group_id) or {"group_id": group_id}
    gateway = _live_gateway()
    gateway.edit_corp_tag_live(tag_or_group_id=group_id, name=group_name)
    sync = _sync_live_projection(command=command, gateway=gateway)
    group = {**before, "group_name": group_name, "source": "live_wecom_tag_write"}
    plan = _create_live_side_effect_plan(
        command=command,
        effect_type="wecom.tag_group.update",
        target_type="wecom_tag_group",
        target_id=group_id,
        payload_summary={"group_id": group_id, "group_name": group_name},
    )
    return _live_write_response(target_id=group_id, side_effect_plan=plan, sync=sync, group=group)


def _handle_live_delete_group(command: Command) -> dict[str, Any]:
    group_id = str(command.payload.get("target_id") or "").strip()
    if not group_id:
        raise ValueError("group_id is required")
    group = _write_repository().get_group(group_id) or {"group_id": group_id}
    gateway = _live_gateway()
    gateway.delete_corp_tag_live(group_ids=[group_id])
    sync = _sync_live_projection(command=command, gateway=gateway)
    plan = _create_live_side_effect_plan(
        command=command,
        effect_type="wecom.tag_group.delete",
        target_type="wecom_tag_group",
        target_id=group_id,
        payload_summary={"group_id": group_id},
        risk_level="high",
    )
    return _live_write_response(target_id=group_id, side_effect_plan=plan, sync=sync, group=group, deleted=True)


def _tag_from_add_result(live_result: dict[str, Any], *, group_id: str, tag_name: str) -> dict[str, Any]:
    group = dict(live_result.get("tag_group") or {})
    tags = list(group.get("tag") or [])
    selected = next((dict(tag or {}) for tag in tags if str((tag or {}).get("name") or (tag or {}).get("tag_name") or "").strip() == tag_name), dict(tags[0] or {}) if tags else {})
    tag_id = str(selected.get("id") or selected.get("tag_id") or "").strip()
    if not tag_id:
        raise ValueError("live WeCom tag creation did not return tag id")
    return {
        "tag_id": tag_id,
        "tag_group_id": str(group.get("group_id") or group_id or "").strip(),
        "tag_name": str(selected.get("name") or selected.get("tag_name") or tag_name).strip(),
        "group_id": str(group.get("group_id") or group_id or "").strip(),
        "group_name": str(group.get("group_name") or "").strip(),
        "order": int(selected.get("order") or 0),
        "status": "active",
        "source": "live_wecom_tag_write",
    }


def _group_from_add_result(live_result: dict[str, Any], *, group_name: str) -> dict[str, Any]:
    group = dict(live_result.get("tag_group") or {})
    group_id = str(group.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("live WeCom tag-group creation did not return group id")
    return {
        "group_id": group_id,
        "tag_group_id": group_id,
        "group_key": group_id,
        "group_name": str(group.get("group_name") or group_name).strip(),
        "tag_count": len(list(group.get("tag") or [])),
        "source": "live_wecom_tag_write",
    }


def _live_write_response(*, target_id: str, side_effect_plan: SideEffectPlan, sync: dict[str, Any], **payload: Any) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "write_model_status": "live_wecom_synced",
        "local_projection_updated": True,
        "real_external_call_executed": True,
        "sync_executed": True,
        "local_only": False,
        "sync": sync,
        "side_effect_plan": _plan_response(side_effect_plan),
        **payload,
    }


def _create_side_effect_plan(
    *,
    command: Command,
    effect_type: str,
    target_type: str,
    target_id: str,
    payload_summary: dict[str, Any],
    risk_level: str,
) -> SideEffectPlan:
    return _side_effect_plans.create_plan(
        command_id=command.command_id,
        effect_type=effect_type,
        adapter_name="wecom_tag_admin",
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


def _create_live_side_effect_plan(
    *,
    command: Command,
    effect_type: str,
    target_type: str,
    target_id: str,
    payload_summary: dict[str, Any],
    risk_level: str = "medium",
) -> SideEffectPlan:
    return _side_effect_plans.create_plan(
        command_id=command.command_id,
        effect_type=effect_type,
        adapter_name="wecom_tag_admin",
        adapter_mode="live_wecom_tag_write",
        target_type=target_type,
        target_id=target_id,
        payload={
            "payload_summary": payload_summary,
            "real_external_call_executed": True,
            "sync_executed": True,
        },
        status="succeeded",
        risk_level=risk_level,
        requires_approval=False,
    )


def _plan_response(plan: SideEffectPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    summary = dict(payload.pop("payload") or {})
    payload["payload_summary"] = summary.get("payload_summary") or {}
    payload["real_external_call_executed"] = bool(summary.get("real_external_call_executed"))
    payload["sync_executed"] = bool(summary.get("sync_executed"))
    return payload


def _response_from_result(result: CommandResult, payload: dict[str, Any]) -> dict[str, Any]:
    response = {
        "ok": result.status in {"completed", "dry_run"},
        "command_id": result.command_id,
        "command_name": result.command_name,
        "idempotency_key": result.idempotency_key,
        "source_status": "next_command",
        "write_model_status": payload.get("write_model_status") or "local_projection_updated",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": bool(payload.get("real_external_call_executed")),
        "sync_executed": bool(payload.get("sync_executed") or False),
        "local_only": bool(payload.get("local_only", True)),
        "audit_recorded": True,
        "command_result_status": result.status,
        "target_id": payload.get("target_id") or "",
    }
    response.update(payload)
    return response


def _target_type(command_name: str) -> str:
    if "tag_group" in command_name:
        return "wecom_tag_group"
    if command_name.endswith(".sync"):
        return "wecom_tag_catalog"
    return "wecom_tag"


reset_wecom_tag_write_fixture_state()
