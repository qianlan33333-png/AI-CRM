from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aicrm_next.admin_jobs.routes import (
    _action_token_error,
    _operator_from_request,
    _request_payload,
)
from aicrm_next.admin_shell import admin_path_for, shell_context
from aicrm_next.shared.admin_action_runtime import ensure_admin_action_token
from aicrm_next.platform_foundation.internal_run_due_guard import maybe_guard_internal_run_due_request
from aicrm_next.platform_foundation.external_effects.test_receiver import safe_current_base_url

from .application import (
    ApproveCloudPlanCommand,
    ApproveCloudPlanRecipientCommand,
    CloudPlanNotFoundError,
    GetCloudPlanQuery,
    GetCloudPlanRecipientQuery,
    ListCloudPlanRecipientsQuery,
    ListCloudPlansQuery,
    RejectCloudPlanCommand,
    RejectCloudPlanRecipientCommand,
    UpdateCloudPlanRecipientMessageCommand,
)
from .campaigns_read import (
    GetCloudCampaignQuery,
    ListCloudCampaignMembersQuery,
    ListCloudCampaignStepsQuery,
    ListCloudCampaignsQuery,
    ROUTE_OWNER as CAMPAIGN_READ_ROUTE_OWNER,
    SOURCE_STATUS as CAMPAIGN_READ_SOURCE_STATUS,
)
from .campaigns_write import (
    AddCloudCampaignStepCommand,
    ApproveCloudCampaignCommand,
    BatchStartCloudCampaignsCommand,
    CloudCampaignWriteInputError,
    CloudCampaignWriteNotFoundError,
    CreateCloudCampaignCommand,
    DeleteCloudCampaignCommand,
    DeleteCloudCampaignStepCommand,
    PauseCloudCampaignCommand,
    RejectCloudCampaignCommand,
    StartCloudCampaignCommand,
    UpdateCloudCampaignStepCommand,
    execute_cloud_campaign_command,
)
from .media_upload import CloudOrchestratorMediaUploadError, build_upload_command, diagnostics_payload
from .run_due import (
    CloudCampaignRunDueInputError,
    PlanCloudCampaignRunDueCommand,
    PreviewCloudCampaignRunDueCommand,
    diagnostics_payload as run_due_diagnostics_payload,
    execute_cloud_campaign_run_due_command,
    normalize_batch_size,
)
from .review_plans import create_ai_assist_review_plan

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

_MEDIA_UPLOAD_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "X-AICRM-WeCom-Media-Upload-Executed": "false",
}
_CAMPAIGN_READ_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
}
_CAMPAIGN_WRITE_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "X-AICRM-Campaign-Execute-Executed": "false",
    "X-AICRM-WeCom-Send-Executed": "false",
}
_RUN_DUE_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "X-AICRM-Campaign-Runtime-Executed": "false",
    "X-AICRM-Automation-Runtime-Executed": "false",
    "X-AICRM-WeCom-Send-Executed": "false",
}
_OBSERVABILITY_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
}


def _raise(exc: Exception) -> None:
    if isinstance(exc, CloudPlanNotFoundError) or isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _media_error(error: str, *, status_code: int = 400) -> JSONResponse:
    payload = diagnostics_payload()
    payload.update({"ok": False, "error": error})
    return JSONResponse(payload, status_code=status_code, headers=_MEDIA_UPLOAD_HEADERS)


def _media_upload_headers(payload: dict[str, Any]) -> dict[str, str]:
    headers = dict(_MEDIA_UPLOAD_HEADERS)
    headers["X-AICRM-Real-External-Call-Executed"] = "true" if payload.get("real_external_call_executed") else "false"
    headers["X-AICRM-WeCom-Media-Upload-Executed"] = "true" if payload.get("wecom_media_upload_executed") else "false"
    return headers


def _campaign_read_error(error: str, *, status_code: int = 404) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": error,
            "source_status": CAMPAIGN_READ_SOURCE_STATUS,
            "route_owner": CAMPAIGN_READ_ROUTE_OWNER,
            "fallback_used": False,
            "real_external_call_executed": False,
        },
        status_code=status_code,
        headers=_CAMPAIGN_READ_HEADERS,
    )


def _campaign_write_error(error: str, *, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": error,
            "source_status": "next_command",
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "adapter_mode": "real_blocked",
            "real_external_call_executed": False,
            "campaign_execute_executed": False,
            "wecom_send_executed": False,
        },
        status_code=status_code,
        headers=_CAMPAIGN_WRITE_HEADERS,
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _observability_payload(source_status: str) -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": source_status,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


def _observability_json(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers=_OBSERVABILITY_HEADERS)


def _observability_options(source_status: str) -> JSONResponse:
    payload = _observability_payload(source_status)
    payload.update({"allowed": True})
    return _observability_json(payload)


async def _write_context(request: Request) -> tuple[dict[str, Any], str | None]:
    payload = await _request_payload(request)
    token_error = await _action_token_error(request, payload)
    if token_error:
        return payload, token_error
    return payload, None


async def _campaign_write_payload(request: Request) -> dict[str, Any]:
    payload = await _request_payload(request)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise CloudCampaignWriteInputError("payload must be an object")
    return payload


def _campaign_actor(request: Request, payload: dict[str, Any]) -> str:
    return str(payload.get("operator") or payload.get("actor_id") or request.headers.get("X-AICRM-Actor") or "admin_ui").strip()


def _campaign_idempotency_key(request: Request, payload: dict[str, Any]) -> str:
    return str(request.headers.get("Idempotency-Key") or payload.get("idempotency_key") or "").strip()


def _campaign_trace_id(request: Request, payload: dict[str, Any]) -> str:
    return str(request.headers.get("X-AICRM-Trace-Id") or payload.get("trace_id") or "").strip()


def _campaign_command_common(request: Request, payload: dict[str, Any], source_route: str) -> dict[str, Any]:
    return {
        "payload": {key: value for key, value in payload.items() if key not in {"operator", "actor_id", "actor_type", "idempotency_key", "dry_run", "trace_id", "command_id"}},
        "idempotency_key": _campaign_idempotency_key(request, payload),
        "actor_id": _campaign_actor(request, payload),
        "actor_type": str(payload.get("actor_type") or "admin").strip(),
        "source_route": source_route,
        "dry_run": bool(payload.get("dry_run", False)),
        "trace_id": _campaign_trace_id(request, payload),
    }


def _campaign_write_response(command) -> JSONResponse:
    try:
        payload = execute_cloud_campaign_command(command)
    except CloudCampaignWriteNotFoundError as exc:
        return _campaign_write_error(str(exc) or "campaign_not_found", status_code=404)
    except CloudCampaignWriteInputError as exc:
        return _campaign_write_error(str(exc) or "invalid_campaign_command", status_code=400)
    except Exception as exc:
        return _campaign_write_error(str(exc) or "campaign_command_unavailable", status_code=503)
    return JSONResponse(payload, headers=_CAMPAIGN_WRITE_HEADERS)


def _run_due_error(error: str, *, status_code: int = 400) -> JSONResponse:
    payload = run_due_diagnostics_payload()
    payload.update(
        {
            "ok": False,
            "error": error,
            "source_status": "next_run_due_plan",
            "processed_count": 0,
            "planned_count": 0,
            "sent_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "candidate_count": 0,
            "candidates": [],
        }
    )
    return JSONResponse(payload, status_code=status_code, headers=_RUN_DUE_HEADERS)


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


def _receiver_response_status_payload(value: Any) -> int:
    try:
        status = int(value or 200)
    except (TypeError, ValueError):
        return 200
    return status if status in {200, 400, 500} else 200


def _ai_assist_external_effect_test_mode_enabled() -> bool:
    return str(os.getenv("AI_ASSIST_EXTERNAL_EFFECT_TEST_MODE", "") or "").strip().lower() in {"1", "true", "yes", "on"}


async def _run_due_payload(request: Request) -> dict[str, Any]:
    payload = await _request_payload(request)
    merged = dict(payload or {})
    for key in (
        "batch_size",
        "dry_run",
        "force_plan",
        "now",
        "preview",
        "scheduled_safe_mode",
        "allow_campaign_ids",
        "expected_due_count",
        "due_count",
        "test_only",
        "receiver_response_status",
    ):
        if key not in merged and key in request.query_params:
            merged[key] = request.query_params.get(key)
    return merged


def _run_due_actor(request: Request, payload: dict[str, Any]) -> str:
    return str(payload.get("operator") or payload.get("actor_id") or request.headers.get("X-AICRM-Actor") or "timer").strip()


def _run_due_common(request: Request, payload: dict[str, Any], source_route: str) -> dict[str, Any]:
    test_only = _bool_payload(payload.get("test_only"), default=False)
    loopback_requested = test_only or _ai_assist_external_effect_test_mode_enabled()
    return {
        "idempotency_key": str(request.headers.get("Idempotency-Key") or payload.get("idempotency_key") or "").strip(),
        "actor_id": _run_due_actor(request, payload),
        "actor_type": str(payload.get("actor_type") or "timer").strip(),
        "batch_size": normalize_batch_size(payload.get("batch_size")),
        "dry_run": _bool_payload(payload.get("dry_run"), default=True),
        "source_route": source_route,
        "trace_id": str(request.headers.get("X-AICRM-Trace-Id") or payload.get("trace_id") or "").strip(),
        "now": str(payload.get("now") or "").strip(),
        "test_only": test_only,
        "test_receiver_base_url": safe_current_base_url(request) if loopback_requested else "",
        "receiver_response_status": _receiver_response_status_payload(payload.get("receiver_response_status")),
    }


def _run_due_response(command) -> JSONResponse:
    try:
        payload = execute_cloud_campaign_run_due_command(command)
    except CloudCampaignRunDueInputError as exc:
        return _run_due_error(str(exc) or "input_error", status_code=400)
    except Exception as exc:
        return _run_due_error(str(exc) or "run_due_unavailable", status_code=503)
    return JSONResponse(payload, headers=_RUN_DUE_HEADERS)


@router.get(
    "/admin/cloud-orchestrator",
    name="api.admin_cloud_orchestrator_workspace",
)
def admin_cloud_orchestrator(request: Request):
    return RedirectResponse(
        url=admin_path_for("api.admin_cloud_orchestrator_plans_workspace"),
        status_code=302,
    )


@router.get(
    "/admin/cloud-orchestrator/plans",
    response_class=HTMLResponse,
    name="api.admin_cloud_orchestrator_plans_workspace",
)
def admin_cloud_plans(request: Request):
    context = shell_context(
        request=request,
        page_title="AI 助手 · 运营计划审阅",
        page_summary="计划列表、目标人员明细与逐人审批。",
        active_endpoint="api.admin_cloud_orchestrator_workspace",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
                {"label": "AI 助手", "href": request.url_for("api.admin_cloud_orchestrator_workspace")},
                {"label": "运营计划审阅"},
            ],
            "page_mode": "list",
            "plan_id": "",
            "admin_action_token": ensure_admin_action_token(),
        }
    )
    return templates.TemplateResponse(request, "admin_console/cloud_plan_review.html", context)


@router.get(
    "/admin/cloud-orchestrator/campaigns",
    response_class=HTMLResponse,
    name="api.admin_cloud_orchestrator_campaigns_workspace",
)
def admin_cloud_campaigns(request: Request):
    context = shell_context(
        request=request,
        page_title="AI 助手 · 运营计划审阅",
        page_summary="Agent 上架的多分层多步骤运营计划在这里审阅启动。",
        active_endpoint="api.admin_cloud_orchestrator_workspace",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
                {"label": "AI 助手", "href": request.url_for("api.admin_cloud_orchestrator_workspace")},
                {"label": "运营计划审阅"},
            ],
            "page_actions": [
                {
                    "label": "可观察性",
                    "href": "/admin/cloud-orchestrator/observability",
                    "variant": "ghost",
                },
            ],
        }
    )
    return templates.TemplateResponse(request, "admin_console/cloud_campaigns_workspace.html", context)


@router.get(
    "/admin/cloud-orchestrator/observability",
    response_class=HTMLResponse,
    name="api.admin_cloud_orchestrator_observability",
)
def admin_cloud_orchestrator_observability(request: Request):
    context = shell_context(
        request=request,
        page_title="Cloud Orchestrator · 可观察性",
        page_summary="工单、审计、漏斗与 Tool 调用统计按 trace_id 串联排查。",
        active_endpoint="api.admin_cloud_orchestrator_workspace",
    )
    context["breadcrumbs"] = [
        {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
        {"label": "AI 助手", "href": request.url_for("api.admin_cloud_orchestrator_workspace")},
        {"label": "可观察性"},
    ]
    context["page_actions"] = [
        {
            "label": "返回助手",
            "href": request.url_for("api.admin_cloud_orchestrator_campaigns_workspace"),
            "variant": "primary",
        },
    ]
    return templates.TemplateResponse(request, "admin_console/cloud_observability.html", context)


@router.api_route("/api/admin/cloud-orchestrator/audit", methods=["GET", "OPTIONS"])
async def api_cloud_orchestrator_audit(request: Request) -> JSONResponse:
    if request.method.upper() == "OPTIONS":
        return _observability_options("next_cloud_orchestrator_audit")

    payload = _observability_payload("next_cloud_orchestrator_audit")
    payload.update(
        {
            "items": [],
            "audit": [],
            "count": 0,
            "limit": int(request.query_params.get("limit") or 100),
            "cursor": _text(request.query_params.get("cursor")),
            "campaign_code": _text(request.query_params.get("campaign_code")),
            "trace_id": _text(request.query_params.get("trace_id")),
            "session_id": _text(request.query_params.get("session_id")),
            "degraded": False,
            "warnings": [],
        }
    )
    return _observability_json(payload)


@router.api_route("/api/admin/cloud-orchestrator/observability", methods=["GET", "OPTIONS"])
async def api_cloud_orchestrator_observability(request: Request) -> JSONResponse:
    if request.method.upper() == "OPTIONS":
        return _observability_options("next_cloud_orchestrator_observability")

    payload = _observability_payload("next_cloud_orchestrator_observability")
    payload.update(
        {
            "health": {"status": "ok", "source": "local_contract"},
            "metrics": {},
            "recent_runs": [],
            "plan_funnel_7d": {},
            "audit_status_1d": {},
            "tool_stats_1d": [],
            "recent_errors": [],
            "degraded": False,
            "warnings": [],
        }
    )
    return _observability_json(payload)


@router.options("/api/admin/cloud-orchestrator/media/upload")
def api_cloud_orchestrator_media_upload_options() -> JSONResponse:
    payload = diagnostics_payload()
    payload.update({"allowed_methods": ["POST", "OPTIONS"]})
    return JSONResponse(payload, headers=_MEDIA_UPLOAD_HEADERS)


@router.post("/api/admin/cloud-orchestrator/media/upload")
async def api_cloud_orchestrator_media_upload(
    request: Request,
    image: UploadFile | None = File(default=None),
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> JSONResponse:
    if image is None or not image.filename:
        return _media_error("missing_image")
    content_type = str(image.content_type or "").strip().lower()
    if not content_type.startswith("image/"):
        return _media_error("invalid_content_type")
    file_bytes = await image.read()
    operator = str(request.headers.get("X-AICRM-Actor") or "admin_ui").strip()
    trace_id = str(request.headers.get("X-AICRM-Trace-Id") or "").strip()
    command = build_upload_command(
        idempotency_key=idempotency_key,
        actor_id=operator,
        actor_type="admin",
        trace_id=trace_id,
    )
    try:
        payload = command(file_name=image.filename, file_bytes=file_bytes, content_type=content_type)
    except ValueError as exc:
        return _media_error(str(exc))
    except CloudOrchestratorMediaUploadError as exc:
        return _media_error(str(exc), status_code=502)
    return JSONResponse(payload, headers=_media_upload_headers(payload))


@router.get("/api/admin/cloud-orchestrator/campaigns")
def api_list_cloud_campaigns(
    review_status: str = "",
    run_status: str = "",
    group_code: str = "",
    limit: int = 5000,
    offset: int = 0,
) -> JSONResponse:
    payload = ListCloudCampaignsQuery()(
        review_status=review_status,
        run_status=run_status,
        group_code=group_code,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(payload, headers=_CAMPAIGN_READ_HEADERS)


@router.post("/api/admin/cloud-orchestrator/campaigns")
async def api_create_cloud_campaign(request: Request) -> JSONResponse:
    payload = await _campaign_write_payload(request)
    campaign_code = str(payload.get("campaign_code") or "").strip()
    command = CreateCloudCampaignCommand(
        campaign_code=campaign_code,
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns",
        ),
    )
    return _campaign_write_response(command)


@router.get("/api/admin/cloud-orchestrator/campaigns/{campaign_code}")
def api_get_cloud_campaign(campaign_code: str) -> JSONResponse:
    try:
        payload = GetCloudCampaignQuery()(campaign_code)
    except LookupError as exc:
        return _campaign_read_error(str(exc) or "campaign_not_found")
    except Exception as exc:
        return _campaign_read_error(str(exc) or "campaign_read_unavailable", status_code=503)
    return JSONResponse(payload, headers=_CAMPAIGN_READ_HEADERS)


@router.get("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/members")
def api_list_cloud_campaign_members(campaign_code: str, status: str = "", limit: int = 200, offset: int = 0) -> JSONResponse:
    try:
        payload = ListCloudCampaignMembersQuery()(campaign_code, status=status, limit=limit, offset=offset)
    except LookupError as exc:
        return _campaign_read_error(str(exc) or "campaign_not_found")
    except Exception as exc:
        return _campaign_read_error(str(exc) or "campaign_members_unavailable", status_code=503)
    return JSONResponse(payload, headers=_CAMPAIGN_READ_HEADERS)


@router.get("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps")
def api_list_cloud_campaign_steps(campaign_code: str) -> JSONResponse:
    try:
        payload = ListCloudCampaignStepsQuery()(campaign_code)
    except LookupError as exc:
        return _campaign_read_error(str(exc) or "campaign_not_found")
    except Exception as exc:
        return _campaign_read_error(str(exc) or "campaign_steps_unavailable", status_code=503)
    return JSONResponse(payload, headers=_CAMPAIGN_READ_HEADERS)


@router.options("/api/admin/cloud-orchestrator/campaigns/run-due")
def api_cloud_campaign_run_due_options() -> JSONResponse:
    payload = run_due_diagnostics_payload()
    payload["source_status"] = "next_run_due_plan"
    return JSONResponse(payload, headers=_RUN_DUE_HEADERS)


@router.options("/api/admin/cloud-orchestrator/campaigns/run-due/preview")
def api_cloud_campaign_run_due_preview_options() -> JSONResponse:
    payload = run_due_diagnostics_payload()
    payload["source_status"] = "next_run_due_preview"
    return JSONResponse(payload, headers=_RUN_DUE_HEADERS)


@router.post("/api/admin/cloud-orchestrator/campaigns/run-due/preview")
async def api_preview_cloud_campaign_run_due(request: Request) -> JSONResponse:
    try:
        payload = await _run_due_payload(request)
        guard_response = maybe_guard_internal_run_due_request(
            request=request,
            payload=payload,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due/preview",
            route_kind="cloud_campaign_run_due_preview",
        )
        if guard_response is not None:
            return guard_response
        command = PreviewCloudCampaignRunDueCommand(
            **_run_due_common(
                request,
                payload,
                "/api/admin/cloud-orchestrator/campaigns/run-due/preview",
            )
        )
    except CloudCampaignRunDueInputError as exc:
        return _run_due_error(str(exc) or "input_error", status_code=400)
    return _run_due_response(command)


@router.post("/api/admin/cloud-orchestrator/campaigns/run-due")
async def api_plan_cloud_campaign_run_due(request: Request) -> JSONResponse:
    try:
        payload = await _run_due_payload(request)
        guard_response = maybe_guard_internal_run_due_request(
            request=request,
            payload=payload,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            route_kind="cloud_campaign_run_due",
        )
        if guard_response is not None:
            return guard_response
        common = _run_due_common(request, payload, "/api/admin/cloud-orchestrator/campaigns/run-due")
        if _bool_payload(payload.get("preview"), default=False):
            command = PreviewCloudCampaignRunDueCommand(**common)
        else:
            command = PlanCloudCampaignRunDueCommand(
                force_plan=_bool_payload(payload.get("force_plan"), default=True),
                **common,
            )
    except CloudCampaignRunDueInputError as exc:
        return _run_due_error(str(exc) or "input_error", status_code=400)
    return _run_due_response(command)


@router.post("/api/admin/cloud-orchestrator/campaigns/batch-start")
async def api_batch_start_cloud_campaigns(request: Request) -> JSONResponse:
    try:
        payload = await _campaign_write_payload(request)
    except CloudCampaignWriteInputError as exc:
        return _campaign_write_error(str(exc))
    codes = tuple(str(code).strip() for code in (payload.get("campaign_codes") or []) if str(code).strip())
    command = BatchStartCloudCampaignsCommand(
        campaign_codes=codes,
        group_code=str(payload.get("group_code") or "").strip(),
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns/batch-start",
        ),
    )
    return _campaign_write_response(command)


@router.post("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/approve")
async def api_approve_cloud_campaign(campaign_code: str, request: Request) -> JSONResponse:
    payload = await _campaign_write_payload(request)
    command = ApproveCloudCampaignCommand(
        campaign_code=campaign_code,
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns/{campaign_code}/approve",
        ),
    )
    return _campaign_write_response(command)


@router.post("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/reject")
async def api_reject_cloud_campaign(campaign_code: str, request: Request) -> JSONResponse:
    payload = await _campaign_write_payload(request)
    command = RejectCloudCampaignCommand(
        campaign_code=campaign_code,
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns/{campaign_code}/reject",
        ),
    )
    return _campaign_write_response(command)


@router.post("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/start")
async def api_start_cloud_campaign(campaign_code: str, request: Request) -> JSONResponse:
    payload = await _campaign_write_payload(request)
    command = StartCloudCampaignCommand(
        campaign_code=campaign_code,
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns/{campaign_code}/start",
        ),
    )
    return _campaign_write_response(command)


@router.post("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/pause")
async def api_pause_cloud_campaign(campaign_code: str, request: Request) -> JSONResponse:
    payload = await _campaign_write_payload(request)
    command = PauseCloudCampaignCommand(
        campaign_code=campaign_code,
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns/{campaign_code}/pause",
        ),
    )
    return _campaign_write_response(command)


@router.delete("/api/admin/cloud-orchestrator/campaigns/{campaign_code}")
async def api_delete_cloud_campaign(campaign_code: str, request: Request) -> JSONResponse:
    payload = await _campaign_write_payload(request)
    command = DeleteCloudCampaignCommand(
        campaign_code=campaign_code,
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns/{campaign_code}",
        ),
    )
    return _campaign_write_response(command)


@router.post("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps")
async def api_add_cloud_campaign_step(campaign_code: str, request: Request) -> JSONResponse:
    payload = await _campaign_write_payload(request)
    command = AddCloudCampaignStepCommand(
        campaign_code=campaign_code,
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps",
        ),
    )
    return _campaign_write_response(command)


@router.post("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps/{step_index}")
@router.patch("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps/{step_index}")
async def api_update_cloud_campaign_step(campaign_code: str, step_index: int, request: Request) -> JSONResponse:
    payload = await _campaign_write_payload(request)
    command = UpdateCloudCampaignStepCommand(
        campaign_code=campaign_code,
        step_index=step_index,
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps/{step_index}",
        ),
    )
    return _campaign_write_response(command)


@router.delete("/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps/{step_index}")
async def api_delete_cloud_campaign_step(campaign_code: str, step_index: int, request: Request) -> JSONResponse:
    payload = await _campaign_write_payload(request)
    command = DeleteCloudCampaignStepCommand(
        campaign_code=campaign_code,
        step_index=step_index,
        **_campaign_command_common(
            request,
            payload,
            "/api/admin/cloud-orchestrator/campaigns/{campaign_code}/steps/{step_index}",
        ),
    )
    return _campaign_write_response(command)


@router.get(
    "/admin/cloud-orchestrator/plans/{plan_id}",
    response_class=HTMLResponse,
    name="api.admin_cloud_orchestrator_plan_detail",
)
def admin_cloud_plan_detail(request: Request, plan_id: str):
    context = shell_context(
        request=request,
        page_title="AI 助手 · 计划二级明细",
        page_summary="目标人员列表与单人话术任务审批。",
        active_endpoint="api.admin_cloud_orchestrator_workspace",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": request.url_for("api.admin_console_dashboard")},
                {"label": "AI 助手", "href": request.url_for("api.admin_cloud_orchestrator_workspace")},
                {"label": "运营计划审阅", "href": "/admin/cloud-orchestrator/plans"},
                {"label": "计划二级明细"},
            ],
            "page_mode": "detail",
            "plan_id": plan_id,
            "admin_action_token": ensure_admin_action_token(),
        }
    )
    return templates.TemplateResponse(request, "admin_console/cloud_plan_review.html", context)


@router.get("/api/admin/cloud-orchestrator/plans")
def api_list_cloud_plans(status: str = "", keyword: str = "", limit: int = 20, offset: int = 0) -> dict[str, Any]:
    try:
        return ListCloudPlansQuery()(status=status, keyword=keyword, limit=limit, offset=offset)
    except Exception as exc:
        _raise(exc)


@router.get("/api/admin/cloud-orchestrator/plans/{plan_id}")
def api_get_cloud_plan(plan_id: str) -> dict[str, Any]:
    try:
        return GetCloudPlanQuery()(plan_id)
    except Exception as exc:
        _raise(exc)


@router.get("/api/admin/cloud-orchestrator/plans/{plan_id}/recipients")
def api_list_cloud_plan_recipients(plan_id: str, status: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    try:
        return ListCloudPlanRecipientsQuery()(plan_id, status=status, limit=limit, offset=offset)
    except Exception as exc:
        _raise(exc)


@router.get("/api/admin/cloud-orchestrator/plans/{plan_id}/recipients/{recipient_id}")
def api_get_cloud_plan_recipient(plan_id: str, recipient_id: int) -> dict[str, Any]:
    try:
        return GetCloudPlanRecipientQuery()(plan_id, recipient_id)
    except Exception as exc:
        _raise(exc)


@router.post("/api/admin/cloud-orchestrator/plans/{plan_id}/approve")
async def api_approve_cloud_plan(plan_id: str, request: Request):
    payload, token_error = await _write_context(request)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    try:
        return ApproveCloudPlanCommand()(plan_id, operator=_operator_from_request(request, payload))
    except Exception as exc:
        _raise(exc)


@router.post("/api/admin/ai-assist/review-plans")
async def create_admin_ai_assist_review_plan(request: Request):
    payload, token_error = await _write_context(request)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    try:
        return create_ai_assist_review_plan(payload)
    except ValueError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc) or "review_plan_create_failed", "route_owner": "ai_crm_next"},
            status_code=400,
        )


@router.post("/api/admin/cloud-orchestrator/plans/{plan_id}/reject")
async def api_reject_cloud_plan(plan_id: str, request: Request):
    payload, token_error = await _write_context(request)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    try:
        return RejectCloudPlanCommand()(
            plan_id,
            operator=_operator_from_request(request, payload),
            reason=str(payload.get("reason") or ""),
        )
    except Exception as exc:
        _raise(exc)


@router.post("/api/admin/cloud-orchestrator/plans/{plan_id}/recipients/{recipient_id}/approve")
async def api_approve_cloud_plan_recipient(plan_id: str, recipient_id: int, request: Request):
    payload, token_error = await _write_context(request)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    try:
        return ApproveCloudPlanRecipientCommand()(
            plan_id,
            recipient_id,
            operator=_operator_from_request(request, payload),
        )
    except Exception as exc:
        _raise(exc)


@router.post("/api/admin/cloud-orchestrator/plans/{plan_id}/recipients/{recipient_id}/reject")
async def api_reject_cloud_plan_recipient(plan_id: str, recipient_id: int, request: Request):
    payload, token_error = await _write_context(request)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    try:
        return RejectCloudPlanRecipientCommand()(
            plan_id,
            recipient_id,
            operator=_operator_from_request(request, payload),
            reason=str(payload.get("reason") or ""),
        )
    except Exception as exc:
        _raise(exc)


@router.patch("/api/admin/cloud-orchestrator/plans/{plan_id}/recipients/{recipient_id}/messages/{message_id}")
async def api_update_cloud_plan_recipient_message(plan_id: str, recipient_id: int, message_id: int, request: Request):
    payload, token_error = await _write_context(request)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    try:
        return UpdateCloudPlanRecipientMessageCommand()(
            plan_id,
            recipient_id,
            message_id,
            payload=payload,
            operator=_operator_from_request(request, payload),
        )
    except Exception as exc:
        _raise(exc)
