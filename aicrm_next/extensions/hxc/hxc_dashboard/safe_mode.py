from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from aicrm_next.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform_foundation.command_bus import Command, CommandBus, CommandContext, CommandResult
from aicrm_next.platform_foundation.external_calls import InMemoryExternalCallAttemptRepository
from aicrm_next.platform_foundation.side_effects import InMemorySideEffectPlanRepository, SideEffectPlan

ROUTE_OWNER = "ai_crm_next"
ADAPTER_MODE = "real_blocked"
DEFAULT_ACTOR = "hxc_dashboard"


class HxcDashboardInputError(ValueError):
    pass


@dataclass(frozen=True)
class HxcDashboardCommand:
    command_id: str = field(default_factory=lambda: "cmd_hxc_dashboard_" + uuid4().hex)
    idempotency_key: str = ""
    actor_id: str = DEFAULT_ACTOR
    actor_type: str = "user"
    source_route: str = ""
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    requested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dry_run: bool = True

    command_name = "hxc_dashboard.command.plan"
    operation = "hxc_dashboard.command"
    effect_type = "hxc.command"
    adapter_name = "hxc_dashboard"
    target_type = "hxc_dashboard"
    target_id = "hxc_dashboard"
    risk_level = "high"
    requires_approval = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "source_route": self.source_route,
            "trace_id": self.trace_id,
            "requested_at": self.requested_at,
            "dry_run": self.dry_run,
        }


@dataclass(frozen=True)
class PlanHxcDashboardRefreshCommand(HxcDashboardCommand):
    trigger_source: str = "admin"

    command_name = "hxc_dashboard.refresh.plan"
    operation = "hxc_dashboard.refresh"
    effect_type = "hxc.refresh"
    adapter_name = "hxc_snapshot_refresh"
    target_type = "hxc_dashboard_snapshot"
    target_id = "latest"

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload["trigger_source"] = self.trigger_source
        return payload


@dataclass(frozen=True)
class PlanHxcDirectorySyncCommand(HxcDashboardCommand):
    command_name = "hxc_dashboard.directory_sync.plan"
    operation = "hxc_dashboard.directory_sync"
    effect_type = "hxc.directory_sync"
    adapter_name = "wecom_directory"
    target_type = "wecom_directory"
    target_id = "admin_members"


@dataclass(frozen=True)
class UpsertHxcSendConfigCommand(HxcDashboardCommand):
    sender_userid: str = ""
    display_name: str = ""
    priority: int = 100
    is_active: bool = True

    command_name = "hxc_dashboard.send_config.upsert"
    operation = "hxc_dashboard.send_config.upsert"
    effect_type = "hxc.send_config.upsert"
    adapter_name = "hxc_send_config_store"
    target_type = "hxc_send_config"
    risk_level = "low"
    requires_approval = False

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload.update(
            {
                "sender_userid": self.sender_userid,
                "display_name": self.display_name,
                "priority": self.priority,
                "is_active": self.is_active,
            }
        )
        return payload


@dataclass(frozen=True)
class DeleteHxcSendConfigCommand(HxcDashboardCommand):
    sender_userid: str = ""

    command_name = "hxc_dashboard.send_config.delete"
    operation = "hxc_dashboard.send_config.delete"
    effect_type = "hxc.send_config.delete"
    adapter_name = "hxc_send_config_store"
    target_type = "hxc_send_config"
    risk_level = "low"
    requires_approval = False

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload["sender_userid"] = self.sender_userid
        return payload


@dataclass(frozen=True)
class PlanHxcBroadcastCommand(HxcDashboardCommand):
    external_userids: list[str] = field(default_factory=list)
    content: str = ""
    image_library_ids: list[int] = field(default_factory=list)
    miniprogram_library_id: int | None = None

    command_name = "hxc_dashboard.broadcast.plan"
    operation = "hxc_dashboard.broadcast"
    effect_type = "hxc.broadcast"
    adapter_name = "wecom_broadcast"
    target_type = "hxc_broadcast"
    target_id = "filtered_users"

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload.update(
            {
                "external_userids": list(self.external_userids),
                "content": self.content,
                "image_library_ids": list(self.image_library_ids),
                "miniprogram_library_id": self.miniprogram_library_id,
            }
        )
        return payload


_audit_ledger = InMemoryAuditLedger()
_side_effect_plans = InMemorySideEffectPlanRepository()
_external_call_attempts = InMemoryExternalCallAttemptRepository()
_command_bus = CommandBus()
_send_configs: dict[str, dict[str, Any]] = {}
_directory_members: list[dict[str, Any]] = []


def reset_hxc_safe_mode_fixture_state() -> None:
    global _audit_ledger, _side_effect_plans, _external_call_attempts, _command_bus, _send_configs, _directory_members
    _audit_ledger = InMemoryAuditLedger()
    _side_effect_plans = InMemorySideEffectPlanRepository()
    _external_call_attempts = InMemoryExternalCallAttemptRepository()
    _command_bus = CommandBus(audit_hook=_audit_hook)
    _send_configs = {
        "hxc_sender_fixture": {
            "id": "hxc_sender_fixture",
            "sender_userid": "hxc_sender_fixture",
            "display_name": "HXC Safe Sender",
            "priority": 10,
            "is_active": True,
            "created_at": "",
            "updated_at": "",
        }
    }
    _directory_members = [
        {
            "wecom_userid": "hxc_sender_fixture",
            "display_name": "HXC Safe Sender",
            "position": "运营",
            "wecom_status": 1,
            "is_sender": True,
            "priority": 10,
            "is_active": True,
        },
        {
            "wecom_userid": "hxc_sender_candidate",
            "display_name": "HXC Candidate",
            "position": "班主任",
            "wecom_status": 1,
            "is_sender": False,
            "priority": 100,
            "is_active": True,
        },
    ]
    _register_handlers()


def get_hxc_audit_events() -> list[dict[str, Any]]:
    return [event.to_dict() for event in _audit_ledger.list_events()]


def get_hxc_side_effect_plans() -> list[dict[str, Any]]:
    return [_plan_response(plan) for plan in _side_effect_plans.list_plans()]


def get_hxc_external_call_attempts() -> list[dict[str, Any]]:
    return [attempt.to_dict() for attempt in _external_call_attempts.list_attempts()]


def dashboard_payload() -> dict[str, Any]:
    rows = _fixture_dashboard_rows()
    send_configs = list_send_configs()
    return {
        "ok": True,
        "source_status": "next_hxc_dashboard",
        "route_owner": ROUTE_OWNER,
        "fallback_used": False,
        "real_external_call_executed": False,
        "dashboard_summary": _summary_for_rows(rows),
        "rows": rows,
        "dashboard_rows": rows,
        "send_configs": send_configs,
        "warnings": [
            "HXC dashboard is served by Next safe-mode read model.",
            "Refresh, directory sync, and legacy broadcast are plan-only.",
        ],
        "degraded": True,
        "empty_state": len(rows) == 0,
    }


def send_config_payload() -> dict[str, Any]:
    members = _members_with_configs()
    configs = list_send_configs()
    last_synced = max((str(item.get("synced_at") or "") for item in members), default="")
    return {
        "ok": True,
        "source_status": "next_hxc_send_config",
        "route_owner": ROUTE_OWNER,
        "fallback_used": False,
        "real_external_call_executed": False,
        "send_configs": configs,
        "directory_candidates": members,
        "members": members,
        "directory_count": len(members),
        "sender_count": sum(1 for item in members if item.get("is_sender")),
        "active_sender_count": sum(1 for item in members if item.get("is_sender") and item.get("is_active")),
        "last_synced_at": last_synced or "safe-mode local",
        "warnings": ["Directory candidates are local safe-mode data; no WeCom directory sync is executed."],
        "degraded": True,
        "empty_state": len(members) == 0,
    }


def list_send_configs() -> list[dict[str, Any]]:
    return sorted((dict(item) for item in _send_configs.values()), key=lambda item: (int(item.get("priority") or 100), str(item.get("sender_userid") or "")))


def execute_hxc_command(command: HxcDashboardCommand) -> dict[str, Any]:
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
            dry_run=False,
        ),
    )
    result = _command_bus.execute(platform_command)
    if result.status == "failed":
        raise HxcDashboardInputError(result.error or "hxc dashboard command failed")
    return _response_from_result(result, dict(result.payload))


def normalize_actor(value: Any, fallback: str = DEFAULT_ACTOR) -> str:
    actor = str(value or "").strip()
    return actor or fallback


def normalize_sender_userid(value: Any) -> str:
    sender_userid = str(value or "").strip()
    if not sender_userid:
        raise HxcDashboardInputError("sender_userid required")
    return sender_userid


def normalize_priority(value: Any, *, default: int = 100) -> int:
    try:
        parsed = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise HxcDashboardInputError("priority must be an integer") from exc
    return max(1, min(parsed, 10000))


def normalize_external_userids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    normalized: list[str] = []
    for raw in raw_values:
        item = str(raw or "").strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def normalize_image_library_ids(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    normalized: list[int] = []
    for raw in list(raw_values)[:3]:
        try:
            normalized.append(int(raw))
        except (TypeError, ValueError):
            continue
    return normalized


def normalize_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HxcDashboardInputError("miniprogram_library_id must be an integer") from exc
    return parsed if parsed > 0 else None


def diagnostics_payload(source_status: str) -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": source_status,
        "route_owner": ROUTE_OWNER,
        "fallback_used": False,
        "allowed_methods": ["POST", "OPTIONS"],
        "adapter_mode": ADAPTER_MODE,
        "real_external_call_executed": False,
        "hxc_refresh_executed": False,
        "directory_sync_executed": False,
        "hxc_broadcast_executed": False,
        "wecom_send_executed": False,
        "wecom_api_called": False,
    }


def unknown_path_payload(path: str) -> dict[str, Any]:
    return {
        "ok": False,
        "source_status": "next_hxc_unknown_path",
        "route_owner": ROUTE_OWNER,
        "fallback_used": False,
        "real_external_call_executed": False,
        "error": "not_found",
        "detail": f"HXC dashboard API path is not available: {path}",
    }


def _register_handlers() -> None:
    _command_bus.register(PlanHxcDashboardRefreshCommand.command_name, _handle_refresh_plan)
    _command_bus.register(PlanHxcDirectorySyncCommand.command_name, _handle_directory_sync_plan)
    _command_bus.register(UpsertHxcSendConfigCommand.command_name, _handle_send_config_upsert)
    _command_bus.register(DeleteHxcSendConfigCommand.command_name, _handle_send_config_delete)
    _command_bus.register(PlanHxcBroadcastCommand.command_name, _handle_broadcast_plan)


def _validate_command(command: HxcDashboardCommand) -> None:
    if not command.command_id.strip():
        raise HxcDashboardInputError("command_id is required")
    if not command.source_route.strip():
        raise HxcDashboardInputError("source_route is required")
    if isinstance(command, UpsertHxcSendConfigCommand):
        normalize_sender_userid(command.sender_userid)
        normalize_priority(command.priority)
    if isinstance(command, DeleteHxcSendConfigCommand):
        normalize_sender_userid(command.sender_userid)
    if isinstance(command, PlanHxcBroadcastCommand):
        if not normalize_external_userids(command.external_userids):
            raise HxcDashboardInputError("no targets")
        if not command.content.strip() and not command.image_library_ids and not command.miniprogram_library_id:
            raise HxcDashboardInputError("empty content")


def _handle_refresh_plan(command: Command) -> dict[str, Any]:
    plan, attempt = _blocked_plan_and_attempt(
        command=command,
        command_cls=PlanHxcDashboardRefreshCommand,
        request_summary={"trigger_source": command.payload.get("trigger_source") or "admin"},
    )
    return _planned_payload(
        source_status="next_hxc_refresh_plan",
        status="planned_blocked",
        plan=plan,
        attempt=attempt,
        extra={"hxc_refresh_executed": False, "trigger_source": command.payload.get("trigger_source") or "admin"},
    )


def _handle_directory_sync_plan(command: Command) -> dict[str, Any]:
    plan, attempt = _blocked_plan_and_attempt(
        command=command,
        command_cls=PlanHxcDirectorySyncCommand,
        request_summary={"directory": "admin_members"},
    )
    return _planned_payload(
        source_status="next_hxc_directory_sync_plan",
        status="planned_blocked",
        plan=plan,
        attempt=attempt,
        extra={"directory_sync_executed": False, "wecom_api_called": False},
    )


def _handle_send_config_upsert(command: Command) -> dict[str, Any]:
    sender_userid = normalize_sender_userid(command.payload.get("sender_userid"))
    record = {
        "id": sender_userid,
        "sender_userid": sender_userid,
        "display_name": str(command.payload.get("display_name") or sender_userid).strip() or sender_userid,
        "priority": normalize_priority(command.payload.get("priority")),
        "is_active": bool(command.payload.get("is_active", True)),
        "created_at": _send_configs.get(sender_userid, {}).get("created_at") or "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _send_configs[sender_userid] = record
    return {
        "source_status": "next_hxc_send_config_command",
        "status": "saved",
        "sender_userid": sender_userid,
        "send_config": dict(record),
        "send_configs": list_send_configs(),
        "planned_count": 1,
        "processed_count": 1,
        "failed_count": 0,
        "skipped_count": 0,
    }


def _handle_send_config_delete(command: Command) -> dict[str, Any]:
    sender_userid = normalize_sender_userid(command.payload.get("sender_userid"))
    existed = sender_userid in _send_configs
    _send_configs.pop(sender_userid, None)
    return {
        "source_status": "next_hxc_send_config_command",
        "status": "deleted" if existed else "not_found",
        "sender_userid": sender_userid,
        "not_found": not existed,
        "send_configs": list_send_configs(),
        "planned_count": 1,
        "processed_count": 1 if existed else 0,
        "failed_count": 0,
        "skipped_count": 0 if existed else 1,
    }


def _handle_broadcast_plan(command: Command) -> dict[str, Any]:
    targets = normalize_external_userids(command.payload.get("external_userids"))
    plan, attempt = _blocked_plan_and_attempt(
        command=command,
        command_cls=PlanHxcBroadcastCommand,
        request_summary={
            "target_count": len(targets),
            "content_present": bool(str(command.payload.get("content") or "").strip()),
            "image_count": len(command.payload.get("image_library_ids") or []),
            "miniprogram_present": bool(command.payload.get("miniprogram_library_id")),
        },
    )
    return _planned_payload(
        source_status="next_hxc_broadcast_plan",
        status="planned_blocked",
        plan=plan,
        attempt=attempt,
        extra={
            "external_userids": targets,
            "target_count": len(targets),
            "hxc_broadcast_executed": False,
            "wecom_send_executed": False,
        },
    )


def _blocked_plan_and_attempt(
    *,
    command: Command,
    command_cls: type[HxcDashboardCommand],
    request_summary: dict[str, Any],
) -> tuple[SideEffectPlan, Any]:
    plan = _side_effect_plans.create_plan(
        command_id=command.command_id,
        effect_type=command_cls.effect_type,
        adapter_name=command_cls.adapter_name,
        adapter_mode=ADAPTER_MODE,
        target_type=command_cls.target_type,
        target_id=command_cls.target_id,
        payload={
            "payload_summary": dict(request_summary),
            "real_external_call_executed": False,
            "hxc_refresh_executed": False,
            "directory_sync_executed": False,
            "hxc_broadcast_executed": False,
            "wecom_send_executed": False,
            "wecom_api_called": False,
        },
        status="blocked",
        risk_level=command_cls.risk_level,
        requires_approval=command_cls.requires_approval,
    )
    attempt = _external_call_attempts.record_attempt(
        adapter_name=command_cls.adapter_name,
        adapter_mode=ADAPTER_MODE,
        operation=command_cls.operation,
        request_id=command.command_id,
        trace_id=command.context.trace_id,
        side_effect_plan_id=plan.side_effect_plan_id,
        status="blocked",
        request_summary=request_summary,
        response_summary={
            "blocked": True,
            "real_external_call_executed": False,
            "hxc_refresh_executed": False,
            "directory_sync_executed": False,
            "hxc_broadcast_executed": False,
            "wecom_send_executed": False,
            "wecom_api_called": False,
        },
        error_code="real_blocked",
        error_message="HXC dashboard command is plan-only in Next safe mode.",
    )
    return plan, attempt


def _planned_payload(
    *,
    source_status: str,
    status: str,
    plan: SideEffectPlan,
    attempt: Any,
    extra: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "source_status": source_status,
        "status": status,
        "planned_count": 1,
        "processed_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "skipped_count": 1,
        "side_effect_plan": _plan_response(plan),
        "external_call_attempt": attempt.to_dict(),
        "adapter_mode": ADAPTER_MODE,
        "real_external_call_executed": False,
        "hxc_refresh_executed": False,
        "directory_sync_executed": False,
        "hxc_broadcast_executed": False,
        "wecom_send_executed": False,
        "wecom_api_called": False,
    }
    payload.update(extra)
    return payload


def _fixture_dashboard_rows() -> list[dict[str, Any]]:
    return [
        {
            "mobile_masked": "138****0001",
            "customer_name": "Safe Mode Customer",
            "funnel_state": "only_member",
            "funnel_label": "仅激活未打开",
            "external_userid": "wx_ext_001",
            "owner_userid": "hxc_sender_fixture",
            "in_lead_pool": "✓",
            "in_people": "✓",
            "in_questionnaire": "✓",
            "questionnaire_count": 1,
            "hxc_member_hit": "✓",
            "hxc_user_hit": "✗",
            "msg_user": 0,
            "msg_ai": 0,
        }
    ]


def _summary_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    funnel = {
        "member_and_user": sum(1 for row in rows if row.get("funnel_state") == "member_and_user"),
        "only_member": sum(1 for row in rows if row.get("funnel_state") == "only_member"),
        "user_no_member": sum(1 for row in rows if row.get("funnel_state") == "user_no_member"),
        "inactive": sum(1 for row in rows if row.get("funnel_state") == "inactive"),
    }
    return {
        "total": len(rows),
        "funnel": funnel,
        "latest_refresh": {
            "started_at": "",
            "finished_at": "",
            "status": "next_safe_mode",
        },
    }


def _members_with_configs() -> list[dict[str, Any]]:
    configs = {item["sender_userid"]: item for item in list_send_configs()}
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for member in _directory_members:
        uid = str(member.get("wecom_userid") or "").strip()
        if not uid:
            continue
        cfg = configs.get(uid)
        merged = dict(member)
        merged["is_sender"] = cfg is not None
        merged["priority"] = int((cfg or member).get("priority") or 100)
        merged["is_active"] = bool((cfg or member).get("is_active", True))
        merged["display_name"] = str((cfg or member).get("display_name") or uid)
        members.append(merged)
        seen.add(uid)
    for uid, cfg in configs.items():
        if uid in seen:
            continue
        members.append(
            {
                "wecom_userid": uid,
                "display_name": cfg.get("display_name") or uid,
                "position": "",
                "wecom_status": 0,
                "is_sender": True,
                "priority": int(cfg.get("priority") or 100),
                "is_active": bool(cfg.get("is_active", True)),
            }
        )
    return members


def _plan_response(plan: SideEffectPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    summary = dict(payload.pop("payload") or {})
    payload["payload_summary"] = summary.get("payload_summary") or {}
    payload["real_external_call_executed"] = False
    payload["hxc_refresh_executed"] = False
    payload["directory_sync_executed"] = False
    payload["hxc_broadcast_executed"] = False
    payload["wecom_send_executed"] = False
    payload["wecom_api_called"] = False
    return payload


def _audit_hook(command: Command, result: CommandResult) -> None:
    _audit_ledger.record_event(
        event_type=f"{command.command_name}.{result.status}",
        actor_id=result.actor_id,
        actor_type=result.actor_type,
        target_type="hxc_dashboard",
        target_id=str((command.payload or {}).get("sender_userid") or (command.payload or {}).get("target_id") or "hxc"),
        source_route=result.source_route,
        command_id=result.command_id,
        trace_id=result.trace_id,
        payload={
            "status": result.status,
            "source_status": (result.payload or {}).get("source_status", ""),
            "fallback_used": False,
            "adapter_mode": (result.payload or {}).get("adapter_mode", ADAPTER_MODE),
            "real_external_call_executed": False,
            "hxc_refresh_executed": False,
            "directory_sync_executed": False,
            "hxc_broadcast_executed": False,
            "wecom_send_executed": False,
        },
    )


def _audit_event_for(command_id: str) -> dict[str, Any]:
    for event in reversed(get_hxc_audit_events()):
        if event.get("command_id") == command_id:
            return event
    return {}


def _response_from_result(result: CommandResult, payload: dict[str, Any]) -> dict[str, Any]:
    response = {
        "ok": result.status == "completed",
        "command_id": result.command_id,
        "command_name": result.command_name,
        "idempotency_key": result.idempotency_key,
        "source_status": str(payload.pop("source_status", "") or "next_hxc_command"),
        "route_owner": ROUTE_OWNER,
        "fallback_used": False,
        "real_external_call_executed": False,
        "hxc_refresh_executed": False,
        "directory_sync_executed": False,
        "hxc_broadcast_executed": False,
        "wecom_send_executed": False,
        "wecom_api_called": False,
        "audit_recorded": True,
        "audit_event": _audit_event_for(result.command_id),
        "command_result_status": result.status,
        "actor": {"id": result.actor_id, "type": result.actor_type},
        "source_route": result.source_route,
        "trace_id": result.trace_id,
    }
    response.update(payload)
    return response


reset_hxc_safe_mode_fixture_state()
