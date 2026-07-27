from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from aicrm_next.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform_foundation.command_bus import Command, CommandBus, CommandContext, CommandResult
from aicrm_next.platform_foundation.internal_events.shadow import (
    AI_CAMPAIGN_APPROVED_EVENT_TYPE,
    AI_CAMPAIGN_CREATED_EVENT_TYPE,
    AI_CAMPAIGN_STARTED_EVENT_TYPE,
    emit_ai_campaign_shadow_event,
    safe_emit,
)
from aicrm_next.platform_foundation.side_effects import InMemorySideEffectPlanRepository, SideEffectPlan

from .campaigns_read import build_campaign_read_repository

SOURCE_STATUS = "next_command"
ROUTE_OWNER = "ai_crm_next"
ADAPTER_MODE = "real_blocked"


class CloudCampaignWriteInputError(ValueError):
    pass


class CloudCampaignWriteNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class CloudCampaignWriteCommand:
    campaign_code: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: "cmd_cloud_campaign_" + uuid4().hex)
    idempotency_key: str = ""
    actor_id: str = "admin_ui"
    actor_type: str = "admin"
    source_route: str = ""
    dry_run: bool = False
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    command_name = "cloud_orchestrator.campaign.write"

    def to_payload(self) -> dict[str, Any]:
        return {
            "campaign_code": self.campaign_code,
            "payload": dict(self.payload),
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "source_route": self.source_route,
            "dry_run": self.dry_run,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class CreateCloudCampaignCommand(CloudCampaignWriteCommand):
    command_name = "cloud_orchestrator.campaign.create"


@dataclass(frozen=True)
class ApproveCloudCampaignCommand(CloudCampaignWriteCommand):
    command_name = "cloud_orchestrator.campaign.approve"


@dataclass(frozen=True)
class RejectCloudCampaignCommand(CloudCampaignWriteCommand):
    command_name = "cloud_orchestrator.campaign.reject"


@dataclass(frozen=True)
class StartCloudCampaignCommand(CloudCampaignWriteCommand):
    command_name = "cloud_orchestrator.campaign.start"


@dataclass(frozen=True)
class PauseCloudCampaignCommand(CloudCampaignWriteCommand):
    command_name = "cloud_orchestrator.campaign.pause"


@dataclass(frozen=True)
class DeleteCloudCampaignCommand(CloudCampaignWriteCommand):
    command_name = "cloud_orchestrator.campaign.delete"


@dataclass(frozen=True)
class BatchStartCloudCampaignsCommand(CloudCampaignWriteCommand):
    campaign_codes: tuple[str, ...] = ()
    group_code: str = ""
    command_name = "cloud_orchestrator.campaign.batch_start"

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload["campaign_codes"] = list(self.campaign_codes)
        payload["group_code"] = self.group_code
        return payload


@dataclass(frozen=True)
class AddCloudCampaignStepCommand(CloudCampaignWriteCommand):
    command_name = "cloud_orchestrator.campaign.step.add"


@dataclass(frozen=True)
class UpdateCloudCampaignStepCommand(CloudCampaignWriteCommand):
    step_index: int = 0
    command_name = "cloud_orchestrator.campaign.step.update"

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload["step_index"] = int(self.step_index)
        return payload


@dataclass(frozen=True)
class DeleteCloudCampaignStepCommand(CloudCampaignWriteCommand):
    step_index: int = 0
    command_name = "cloud_orchestrator.campaign.step.delete"

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload["step_index"] = int(self.step_index)
        return payload


_audit_ledger = InMemoryAuditLedger()
_side_effect_plans = InMemorySideEffectPlanRepository()
_command_bus = CommandBus()


def reset_campaign_write_fixture_state() -> None:
    global _audit_ledger, _side_effect_plans, _command_bus
    _audit_ledger = InMemoryAuditLedger()
    _side_effect_plans = InMemorySideEffectPlanRepository()
    _command_bus = CommandBus(audit_hook=_audit_hook)
    _register_handlers()


def get_campaign_write_audit_events() -> list[dict[str, Any]]:
    return [event.to_dict() for event in _audit_ledger.list_events()]


def get_campaign_write_side_effect_plans() -> list[dict[str, Any]]:
    return [_plan_response(plan) for plan in _side_effect_plans.list_plans()]


def execute_cloud_campaign_command(command: CloudCampaignWriteCommand) -> dict[str, Any]:
    _validate_command(command)
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
        error = result.error or "cloud campaign command failed"
        if "campaign_not_found" in error:
            raise CloudCampaignWriteNotFoundError("campaign_not_found")
        raise CloudCampaignWriteInputError(error)
    return _response_from_result(result, dict(result.payload))


def _register_handlers() -> None:
    _command_bus.register(CreateCloudCampaignCommand.command_name, _handle_create)
    _command_bus.register(ApproveCloudCampaignCommand.command_name, _handle_approve)
    _command_bus.register(RejectCloudCampaignCommand.command_name, _handle_reject)
    _command_bus.register(StartCloudCampaignCommand.command_name, _handle_start)
    _command_bus.register(PauseCloudCampaignCommand.command_name, _handle_pause)
    _command_bus.register(DeleteCloudCampaignCommand.command_name, _handle_delete)
    _command_bus.register(BatchStartCloudCampaignsCommand.command_name, _handle_batch_start)
    _command_bus.register(AddCloudCampaignStepCommand.command_name, _handle_step_add)
    _command_bus.register(UpdateCloudCampaignStepCommand.command_name, _handle_step_update)
    _command_bus.register(DeleteCloudCampaignStepCommand.command_name, _handle_step_delete)


def _validate_command(command: CloudCampaignWriteCommand) -> None:
    if not command.command_id.strip():
        raise CloudCampaignWriteInputError("command_id is required")
    if not command.source_route.strip():
        raise CloudCampaignWriteInputError("source_route is required")
    if isinstance(command, BatchStartCloudCampaignsCommand):
        if not command.campaign_codes and not command.group_code.strip():
            raise CloudCampaignWriteInputError("campaign_codes or group_code is required")
        return
    if not command.campaign_code.strip():
        raise CloudCampaignWriteInputError("campaign_code is required")


def _repo() -> Any:
    return build_campaign_read_repository()


def _get_campaign(repo: Any, campaign_code: str) -> dict[str, Any]:
    campaign = repo.get_campaign(campaign_code) if hasattr(repo, "get_campaign") else None
    if not campaign:
        raise CloudCampaignWriteNotFoundError("campaign_not_found")
    return campaign


def _create_campaign(repo: Any, campaign_code: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not hasattr(repo, "create_campaign"):
        raise CloudCampaignWriteInputError("campaign create write model unavailable")
    campaign_payload = dict(payload or {})
    campaign_payload["campaign_code"] = campaign_code
    campaign = repo.create_campaign(campaign_payload)
    if not campaign:
        raise CloudCampaignWriteInputError("campaign_create_failed")
    return campaign


def _update_campaign_status(
    repo: Any,
    campaign_code: str,
    *,
    review_status: str | None = None,
    run_status: str | None = None,
    deleted: bool = False,
) -> dict[str, Any]:
    if hasattr(repo, "update_campaign_status"):
        updated = repo.update_campaign_status(
            campaign_code,
            review_status=review_status,
            run_status=run_status,
            deleted=deleted,
        )
        if updated:
            return updated
    campaign = _get_campaign(repo, campaign_code)
    if review_status is not None:
        campaign["review_status"] = review_status
    if run_status is not None:
        campaign["run_status"] = run_status
    if deleted:
        campaign["review_status"] = "deleted"
        campaign["run_status"] = "cancelled"
    return campaign


def _internal_event_response(internal_event: dict[str, Any]) -> dict[str, Any]:
    return {
        "internal_event_id": internal_event.get("event_id") or "",
        "internal_event_status": internal_event.get("status") or "",
        "internal_event_reason": internal_event.get("reason") or "",
        "internal_event_error": internal_event.get("error") or "",
        "internal_event_consumer_run_count": int(internal_event.get("consumer_run_count") or 0),
    }


def _handle_create(command: Command) -> dict[str, Any]:
    campaign_code = str(command.payload.get("campaign_code") or "").strip()
    repo = _repo()
    campaign = _create_campaign(repo, campaign_code, dict(command.payload.get("payload") or {}))
    internal_event = safe_emit(
        "ai_campaign.created",
        emit_ai_campaign_shadow_event,
        command=command,
        event_type=AI_CAMPAIGN_CREATED_EVENT_TYPE,
        campaign=campaign,
    )
    return {
        "write_model_status": "created",
        "campaign": campaign,
        **_internal_event_response(internal_event),
    }


def _handle_approve(command: Command) -> dict[str, Any]:
    campaign_code = str(command.payload.get("campaign_code") or "").strip()
    repo = _repo()
    campaign = _update_campaign_status(repo, campaign_code, review_status="approved")
    internal_event = safe_emit(
        "ai_campaign.approved",
        emit_ai_campaign_shadow_event,
        command=command,
        event_type=AI_CAMPAIGN_APPROVED_EVENT_TYPE,
        campaign=campaign,
    )
    return {
        "write_model_status": "updated",
        "campaign": campaign,
        **_internal_event_response(internal_event),
    }


def _handle_reject(command: Command) -> dict[str, Any]:
    campaign_code = str(command.payload.get("campaign_code") or "").strip()
    repo = _repo()
    campaign = _update_campaign_status(repo, campaign_code, review_status="rejected", run_status="cancelled")
    return {"write_model_status": "updated", "campaign": campaign}


def _handle_start(command: Command) -> dict[str, Any]:
    campaign_code = str(command.payload.get("campaign_code") or "").strip()
    repo = _repo()
    campaign = _update_campaign_status(repo, campaign_code, review_status="approved", run_status="active")
    plan = _create_start_side_effect_plan(command=command, target_id=campaign_code, payload_summary={"campaign_code": campaign_code})
    internal_event = safe_emit(
        "ai_campaign.started",
        emit_ai_campaign_shadow_event,
        command=command,
        event_type=AI_CAMPAIGN_STARTED_EVENT_TYPE,
        campaign=campaign,
    )
    return {
        "write_model_status": "planned",
        "campaign": campaign,
        "side_effect_plan": _plan_response(plan),
        **_internal_event_response(internal_event),
    }


def _handle_pause(command: Command) -> dict[str, Any]:
    campaign_code = str(command.payload.get("campaign_code") or "").strip()
    repo = _repo()
    campaign = _update_campaign_status(repo, campaign_code, run_status="paused")
    return {"write_model_status": "updated", "campaign": campaign}


def _handle_delete(command: Command) -> dict[str, Any]:
    campaign_code = str(command.payload.get("campaign_code") or "").strip()
    repo = _repo()
    campaign = _update_campaign_status(repo, campaign_code, deleted=True)
    return {"write_model_status": "deleted", "campaign": campaign}


def _handle_batch_start(command: Command) -> dict[str, Any]:
    repo = _repo()
    payload = dict(command.payload)
    codes = [str(code).strip() for code in payload.get("campaign_codes") or [] if str(code).strip()]
    group_code = str(payload.get("group_code") or "").strip()
    if not codes and group_code and hasattr(repo, "list_campaigns"):
        rows, _total = repo.list_campaigns(group_code=group_code, limit=5000, offset=0)
        codes = [str(row.get("campaign_code") or "").strip() for row in rows if str(row.get("campaign_code") or "").strip()]
    if not codes:
        raise CloudCampaignWriteInputError("campaign_codes or group_code is required")

    started: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for code in codes:
        try:
            started.append(_update_campaign_status(repo, code, review_status="approved", run_status="active"))
        except CloudCampaignWriteNotFoundError:
            failed.append({"campaign_code": code, "error": "campaign_not_found"})
    if not started and failed:
        raise CloudCampaignWriteNotFoundError("campaign_not_found")
    plan = _create_start_side_effect_plan(command=command, target_id=group_code or ",".join(codes), payload_summary={"campaign_codes": codes, "group_code": group_code})
    return {
        "write_model_status": "planned",
        "campaigns": started,
        "started_count": len(started),
        "skipped_count": 0,
        "failed_count": len(failed),
        "failed": failed,
        "side_effect_plan": _plan_response(plan),
    }


def _handle_step_add(command: Command) -> dict[str, Any]:
    repo = _repo()
    campaign_code = str(command.payload.get("campaign_code") or "").strip()
    _get_campaign(repo, campaign_code)
    if not hasattr(repo, "add_step"):
        raise CloudCampaignWriteInputError("campaign step write model unavailable")
    step = repo.add_step(campaign_code, dict(command.payload.get("payload") or {}))
    if not step:
        raise CloudCampaignWriteNotFoundError("campaign_not_found")
    return {"write_model_status": "updated", "step": step}


def _handle_step_update(command: Command) -> dict[str, Any]:
    repo = _repo()
    campaign_code = str(command.payload.get("campaign_code") or "").strip()
    _get_campaign(repo, campaign_code)
    if not hasattr(repo, "update_step"):
        raise CloudCampaignWriteInputError("campaign step write model unavailable")
    step = repo.update_step(campaign_code, int(command.payload.get("step_index") or 0), dict(command.payload.get("payload") or {}))
    if not step:
        raise CloudCampaignWriteNotFoundError("campaign_step_not_found")
    return {"write_model_status": "updated", "step": step}


def _handle_step_delete(command: Command) -> dict[str, Any]:
    repo = _repo()
    campaign_code = str(command.payload.get("campaign_code") or "").strip()
    _get_campaign(repo, campaign_code)
    if not hasattr(repo, "delete_step"):
        raise CloudCampaignWriteInputError("campaign step write model unavailable")
    step = repo.delete_step(campaign_code, int(command.payload.get("step_index") or 0))
    if not step:
        raise CloudCampaignWriteNotFoundError("campaign_step_not_found")
    return {"write_model_status": "deleted", "step": step}


def _create_start_side_effect_plan(*, command: Command, target_id: str, payload_summary: dict[str, Any]) -> SideEffectPlan:
    return _side_effect_plans.create_plan(
        command_id=command.command_id,
        effect_type="cloud_orchestrator.campaign.start",
        adapter_name="cloud_orchestrator_command_bus",
        adapter_mode=ADAPTER_MODE,
        target_type="cloud_campaign",
        target_id=target_id,
        payload={
            "payload_summary": payload_summary,
            "real_external_call_executed": False,
            "campaign_execute_executed": False,
            "wecom_send_executed": False,
        },
        status="planned",
        risk_level="high",
        requires_approval=True,
    )


def _plan_response(plan: SideEffectPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    summary = dict(payload.pop("payload") or {})
    payload["payload_summary"] = summary.get("payload_summary") or {}
    payload["real_external_call_executed"] = False
    payload["campaign_execute_executed"] = False
    payload["wecom_send_executed"] = False
    return payload


def _audit_hook(command: Command, result: CommandResult) -> None:
    _audit_ledger.record_event(
        event_type=f"{command.command_name}.{result.status}",
        actor_id=result.actor_id,
        actor_type=result.actor_type,
        target_type="cloud_campaign",
        target_id=str(command.payload.get("campaign_code") or command.payload.get("group_code") or ""),
        source_route=result.source_route,
        command_id=result.command_id,
        trace_id=result.trace_id,
        payload={
            "status": result.status,
            "fallback_used": False,
            "real_external_call_executed": False,
            "campaign_execute_executed": False,
            "wecom_send_executed": False,
            "write_model_status": (result.payload or {}).get("write_model_status") or "",
        },
    )


def _audit_event_for(command_id: str) -> dict[str, Any]:
    for event in reversed(get_campaign_write_audit_events()):
        if event.get("command_id") == command_id:
            return event
    return {}


def _response_from_result(result: CommandResult, payload: dict[str, Any]) -> dict[str, Any]:
    response = {
        "ok": result.status in {"completed", "dry_run"},
        "command_id": result.command_id,
        "command_name": result.command_name,
        "idempotency_key": result.idempotency_key,
        "source_status": SOURCE_STATUS,
        "write_model_status": payload.get("write_model_status") or "updated",
        "route_owner": ROUTE_OWNER,
        "fallback_used": False,
        "adapter_mode": ADAPTER_MODE,
        "real_external_call_executed": False,
        "campaign_execute_executed": False,
        "wecom_send_executed": False,
        "audit_recorded": True,
        "audit_event": _audit_event_for(result.command_id),
        "command_result_status": result.status,
        "actor": {"id": result.actor_id, "type": result.actor_type},
        "source_route": result.source_route,
        "trace_id": result.trace_id,
        "dry_run": result.status == "dry_run",
    }
    response.update(payload)
    return response


reset_campaign_write_fixture_state()
