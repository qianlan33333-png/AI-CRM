from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from aicrm_next.admin_shell_contract import shell_context
from aicrm_next.shared.errors import ContractError

from .application import CreateHxcBroadcastTaskCommand
from .dto import HxcBroadcastTaskRequest
from .safe_mode import (
    DeleteHxcSendConfigCommand,
    HxcDashboardInputError,
    PlanHxcBroadcastCommand,
    PlanHxcDashboardRefreshCommand,
    PlanHxcDirectorySyncCommand,
    UpsertHxcSendConfigCommand,
    dashboard_payload,
    diagnostics_payload,
    execute_hxc_command,
    normalize_actor,
    normalize_external_userids,
    normalize_image_library_ids,
    normalize_optional_int,
    normalize_priority,
    normalize_sender_userid,
    send_config_payload,
    unknown_path_payload,
)


router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)
_HXC_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "X-AICRM-HXC-Refresh-Executed": "false",
    "X-AICRM-Directory-Sync-Executed": "false",
    "X-AICRM-HXC-Broadcast-Executed": "false",
    "X-AICRM-WeCom-Send-Executed": "false",
}


def _bool_payload(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


async def _json_payload(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            payload = await request.json()
        except Exception as exc:
            raise HxcDashboardInputError("payload must be valid JSON") from exc
    else:
        body = await request.body()
        payload = {} if not body else await request.json()
    if payload is None:
        merged: dict[str, Any] = {}
    elif isinstance(payload, dict):
        merged = dict(payload)
    else:
        raise HxcDashboardInputError("payload must be an object")
    for key in ("trigger_source", "sender_userid", "display_name", "priority", "is_active", "external_userids", "content", "image_library_ids", "miniprogram_library_id"):
        if key not in merged and key in request.query_params:
            merged[key] = request.query_params.get(key)
    return merged


def _common_command(request: Request, payload: dict[str, Any], source_route: str) -> dict[str, Any]:
    return {
        "idempotency_key": str(request.headers.get("Idempotency-Key") or payload.get("idempotency_key") or "").strip(),
        "actor_id": normalize_actor(payload.get("operator_id") or payload.get("operator") or payload.get("actor_id") or request.headers.get("X-AICRM-Actor")),
        "actor_type": str(payload.get("actor_type") or "user").strip(),
        "source_route": source_route,
        "trace_id": str(request.headers.get("X-AICRM-Trace-Id") or payload.get("trace_id") or "").strip(),
        "dry_run": _bool_payload(payload.get("dry_run"), default=True),
    }


def _hxc_error(error: str, *, source_status: str, status_code: int = 400) -> JSONResponse:
    payload = diagnostics_payload(source_status)
    payload.update(
        {
            "ok": False,
            "error": error,
            "status": "input_error" if status_code == 400 else "error",
            "planned_count": 0,
            "processed_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
        }
    )
    return JSONResponse(payload, status_code=status_code, headers=_HXC_HEADERS)


def _hxc_response(command, *, source_status: str) -> JSONResponse:
    try:
        payload = execute_hxc_command(command)
    except HxcDashboardInputError as exc:
        return _hxc_error(str(exc) or "input_error", source_status=source_status, status_code=400)
    except Exception as exc:
        return _hxc_error(str(exc) or "hxc_dashboard_unavailable", source_status=source_status, status_code=503)
    return JSONResponse(payload, headers=_HXC_HEADERS)


@router.get("/admin/hxc-dashboard", name="api.admin_hxc_dashboard_workspace")
def admin_hxc_dashboard_page(request: Request):
    payload = dashboard_payload()
    context = shell_context(
        request=request,
        page_title="用户激活漏斗看板",
        page_summary=(
            "CRM 三表手机号并集 × 黄小璨用户/会员/订阅/测评/成长目标/路径/任务/复盘/V6 角色评分 "
            "聚合, Next safe-mode 提供受控数据与计划型写操作."
        ),
        active_endpoint="api.admin_hxc_dashboard_workspace",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
        {"label": "用户激活漏斗看板"},
    ]
    context.update(
        {
            "dashboard_rows": payload["rows"],
            "dashboard_summary": payload["dashboard_summary"],
            "send_configs": payload["send_configs"],
        }
    )
    return templates.TemplateResponse(request, "admin_console/hxc_dashboard.html", context)


@router.get("/admin/hxc-send-config", name="api.admin_hxc_send_config_page")
def admin_hxc_send_config_page(request: Request):
    payload = send_config_payload()
    context = shell_context(
        request=request,
        page_title="群发发送人管理",
        page_summary="从本地 safe-mode 通讯录候选中选择群发发送人；目录同步为计划型操作.",
        active_endpoint="api.admin_hxc_dashboard_workspace",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
        {"label": "激活漏斗看板", "href": request.url_for("api.admin_hxc_dashboard_workspace")},
        {"label": "群发发送人管理"},
    ]
    context.update(
        {
            "directory_count": payload["directory_count"],
            "sender_count": payload["sender_count"],
            "active_sender_count": payload["active_sender_count"],
            "last_synced_at": payload["last_synced_at"],
            "members": payload["members"],
            "send_configs": payload["send_configs"],
        }
    )
    return templates.TemplateResponse(request, "admin_console/hxc_send_config.html", context)


@router.get("/api/admin/hxc-dashboard")
def get_hxc_dashboard() -> JSONResponse:
    return JSONResponse(dashboard_payload(), headers=_HXC_HEADERS)


@router.options("/api/admin/hxc-dashboard/refresh")
def hxc_dashboard_refresh_options() -> JSONResponse:
    return JSONResponse(diagnostics_payload("next_hxc_refresh_plan"), headers=_HXC_HEADERS)


@router.post("/api/admin/hxc-dashboard/refresh")
async def plan_hxc_dashboard_refresh(request: Request) -> JSONResponse:
    source_status = "next_hxc_refresh_plan"
    try:
        payload = await _json_payload(request)
        command = PlanHxcDashboardRefreshCommand(
            **_common_command(request, payload, "/api/admin/hxc-dashboard/refresh"),
            trigger_source=str(payload.get("trigger_source") or "admin").strip() or "admin",
        )
    except HxcDashboardInputError as exc:
        return _hxc_error(str(exc) or "input_error", source_status=source_status, status_code=400)
    return _hxc_response(command, source_status=source_status)


@router.options("/api/admin/hxc-dashboard/refresh-directory")
def hxc_dashboard_refresh_directory_options() -> JSONResponse:
    return JSONResponse(diagnostics_payload("next_hxc_directory_sync_plan"), headers=_HXC_HEADERS)


@router.post("/api/admin/hxc-dashboard/refresh-directory")
async def plan_hxc_dashboard_refresh_directory(request: Request) -> JSONResponse:
    source_status = "next_hxc_directory_sync_plan"
    try:
        payload = await _json_payload(request)
        command = PlanHxcDirectorySyncCommand(**_common_command(request, payload, "/api/admin/hxc-dashboard/refresh-directory"))
    except HxcDashboardInputError as exc:
        return _hxc_error(str(exc) or "input_error", source_status=source_status, status_code=400)
    return _hxc_response(command, source_status=source_status)


@router.get("/api/admin/hxc-dashboard/send-config")
def get_hxc_send_config() -> JSONResponse:
    return JSONResponse(send_config_payload(), headers=_HXC_HEADERS)


@router.options("/api/admin/hxc-dashboard/send-config")
def hxc_send_config_options() -> JSONResponse:
    return JSONResponse(diagnostics_payload("next_hxc_send_config_command"), headers=_HXC_HEADERS)


@router.post("/api/admin/hxc-dashboard/send-config")
async def upsert_hxc_send_config(request: Request) -> JSONResponse:
    source_status = "next_hxc_send_config_command"
    try:
        payload = await _json_payload(request)
        command = UpsertHxcSendConfigCommand(
            **_common_command(request, payload, "/api/admin/hxc-dashboard/send-config"),
            sender_userid=normalize_sender_userid(payload.get("sender_userid")),
            display_name=str(payload.get("display_name") or "").strip(),
            priority=normalize_priority(payload.get("priority")),
            is_active=_bool_payload(payload.get("is_active"), default=True),
        )
    except HxcDashboardInputError as exc:
        return _hxc_error(str(exc) or "input_error", source_status=source_status, status_code=400)
    return _hxc_response(command, source_status=source_status)


@router.options("/api/admin/hxc-dashboard/send-config/{sender_userid}")
def hxc_send_config_delete_options(sender_userid: str) -> JSONResponse:
    payload = diagnostics_payload("next_hxc_send_config_command")
    payload["sender_userid"] = sender_userid
    return JSONResponse(payload, headers=_HXC_HEADERS)


@router.delete("/api/admin/hxc-dashboard/send-config/{sender_userid}")
async def delete_hxc_send_config(sender_userid: str, request: Request) -> JSONResponse:
    source_status = "next_hxc_send_config_command"
    try:
        payload = await _json_payload(request)
        command = DeleteHxcSendConfigCommand(
            **_common_command(request, payload, f"/api/admin/hxc-dashboard/send-config/{sender_userid}"),
            sender_userid=normalize_sender_userid(sender_userid),
        )
    except HxcDashboardInputError as exc:
        return _hxc_error(str(exc) or "input_error", source_status=source_status, status_code=400)
    return _hxc_response(command, source_status=source_status)


@router.options("/api/admin/hxc-dashboard/broadcast")
def hxc_dashboard_broadcast_options() -> JSONResponse:
    return JSONResponse(diagnostics_payload("next_hxc_broadcast_plan"), headers=_HXC_HEADERS)


@router.post("/api/admin/hxc-dashboard/broadcast")
async def plan_hxc_dashboard_broadcast(request: Request) -> JSONResponse:
    source_status = "next_hxc_broadcast_plan"
    try:
        payload = await _json_payload(request)
        command = PlanHxcBroadcastCommand(
            **_common_command(request, payload, "/api/admin/hxc-dashboard/broadcast"),
            external_userids=normalize_external_userids(payload.get("external_userids")),
            content=str(payload.get("content") or "").strip(),
            image_library_ids=normalize_image_library_ids(payload.get("image_library_ids")),
            miniprogram_library_id=normalize_optional_int(payload.get("miniprogram_library_id")),
        )
    except HxcDashboardInputError as exc:
        return _hxc_error(str(exc) or "input_error", source_status=source_status, status_code=400)
    return _hxc_response(command, source_status=source_status)


@router.post("/api/admin/hxc-dashboard/broadcast-tasks")
def create_hxc_broadcast_task(payload: dict[str, Any]) -> JSONResponse:
    try:
        request = HxcBroadcastTaskRequest.model_validate(payload)
        result = CreateHxcBroadcastTaskCommand()(request)
        return JSONResponse(result, status_code=int(result.get("status_code") or 200))
    except ValidationError:
        return _error("请求参数格式不正确", status_code=400)
    except ContractError as exc:
        return _error(str(exc), status_code=400)
    except Exception as exc:
        return _error(f"HXC 群发任务创建失败：{exc}", status_code=500)


def _error(message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, "detail": message}, status_code=status_code)


@router.api_route("/api/admin/hxc-dashboard/{unknown_path:path}", methods=["GET", "POST", "OPTIONS"])
def hxc_dashboard_unknown_path(unknown_path: str) -> JSONResponse:
    return JSONResponse(unknown_path_payload(unknown_path), status_code=404, headers=_HXC_HEADERS)
