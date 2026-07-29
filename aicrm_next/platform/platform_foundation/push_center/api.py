from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from aicrm_next.platform.shared.admin_action_runtime import validate_admin_action_token
from aicrm_next.platform.platform_foundation.execution_runtime.api_command import (
    QueueCommandPayloadError,
    accepted_queue_command_payload,
    authenticated_queue_actor,
    parse_manual_queue_command,
    submit_manual_queue_action,
)
from aicrm_next.platform.platform_foundation.execution_runtime.commands import (
    QueueCommandConflict,
    QueueCommandDuplicateRiskRequired,
    QueueRuntimeCommandService,
)
from aicrm_next.platform.platform_foundation.execution_runtime.read_model import ExecutionRuntimeReadModel

from . import CAPABILITY_OWNER, ROUTE_OWNER
from .repository import PushCenterRepository
from .view_model import (
    build_job_detail_payload,
    build_job_reconciliation_payload,
    build_jobs_payload,
    build_sections_payload,
    build_stats_payload,
)

router = APIRouter()
_PUSH_LANES = frozenset(
    {
        "ai_generation",
        "wecom_interactive",
        "wecom_bulk",
        "wecom_ai_assistant_bulk",
        "wecom_media",
        "outbound_webhook",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


async def _payload(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except Exception:
        return {}
    return dict(raw or {}) if isinstance(raw, dict) else {}


def _json(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    payload.setdefault("route_owner", ROUTE_OWNER)
    payload.setdefault("real_external_call_executed", False)
    return JSONResponse(
        jsonable_encoder(payload),
        status_code=status_code,
        headers={
            "X-AICRM-Route-Owner": ROUTE_OWNER,
            "X-AICRM-Real-External-Call-Executed": "true" if bool(payload.get("real_external_call_executed")) else "false",
        },
    )


def _action_or_internal_token_error(request: Request, payload: dict[str, Any]) -> str:
    token = _text(request.headers.get("X-Admin-Action-Token")) or _text(payload.get("admin_action_token"))
    return validate_admin_action_token(token, request=request)


def _runtime_queue_summary() -> dict[str, Any]:
    try:
        return ExecutionRuntimeReadModel().lane_summary(_PUSH_LANES)
    except Exception:
        return {}


def _queue_command_service(request: Request) -> QueueRuntimeCommandService:
    service = getattr(request.app.state, "queue_runtime_command_service", None)
    return service if service is not None else QueueRuntimeCommandService()


def _command_payload_error(exc: QueueCommandPayloadError) -> JSONResponse:
    return _json(
        {
            "ok": False,
            "error": "manual_queue_command_fields_required",
            "missing_fields": list(exc.missing_fields),
        },
        status_code=422,
    )


async def _accepted_action_response(
    service: QueueRuntimeCommandService,
    target: Any,
    command: Any,
    *,
    action: str,
    source_route: str,
) -> JSONResponse:
    try:
        result = await run_in_threadpool(
            submit_manual_queue_action,
            service,
            target,
            command,
            action=action,
            source_route=source_route,
        )
    except QueueCommandDuplicateRiskRequired:
        return _json({"ok": False, "error": "duplicate_risk_confirmation_required"}, status_code=409)
    except QueueCommandConflict:
        return _json({"ok": False, "error": "queue_command_cas_conflict"}, status_code=409)
    except ValueError:
        return _json({"ok": False, "error": "queue_command_target_not_eligible"}, status_code=409)
    return _json(accepted_queue_command_payload(result, command), status_code=202)


@router.get("/api/admin/push-center/sections")
def push_center_sections(
    section: str = "",
    effect_type: str = "",
    status: str = "",
    business_type: str = "",
    business_id: str = "",
    target_type: str = "",
    target_id: str = "",
    external_userid: str = "",
    owner_userid: str = "",
    trace_id: str = "",
    idempotency_key: str = "",
    source_module: str = "",
    source_route: str = "",
    created_from: str = "",
    created_to: str = "",
) -> dict[str, Any]:
    return build_sections_payload(locals(), repository=PushCenterRepository())


@router.get("/api/admin/push-center/jobs")
def push_center_jobs(
    section: str = "",
    effect_type: str = "",
    status: str = "",
    business_type: str = "",
    business_id: str = "",
    target_type: str = "",
    target_id: str = "",
    external_userid: str = "",
    owner_userid: str = "",
    trace_id: str = "",
    idempotency_key: str = "",
    source_module: str = "",
    source_route: str = "",
    created_from: str = "",
    created_to: str = "",
    limit: int = 50,
    offset: int = 0,
    cursor: str = "",
) -> JSONResponse:
    payload = build_jobs_payload(locals(), repository=PushCenterRepository())
    payload["runtime_queue"] = _runtime_queue_summary()
    if payload.get("error") == "invalid_push_center_cursor":
        return _json(payload, status_code=422)
    return _json(payload)


@router.get("/api/admin/push-center/jobs/{job_id}")
def push_center_job_detail(job_id: str) -> JSONResponse:
    payload = build_job_detail_payload(job_id, repository=PushCenterRepository())
    if not payload:
        return _json({"ok": False, "error": "push_center_job_not_found"}, status_code=404)
    return _json(payload)


@router.get("/api/admin/push-center/jobs/{job_id}/reconciliation")
def push_center_job_reconciliation(job_id: str) -> JSONResponse:
    payload = build_job_reconciliation_payload(job_id, repository=PushCenterRepository())
    if not payload:
        return _json({"ok": False, "error": "push_center_job_not_found"}, status_code=404)
    return _json(payload)


@router.get("/api/admin/push-center/stats")
def push_center_stats(
    section: str = "",
    effect_type: str = "",
    status: str = "",
    business_type: str = "",
    business_id: str = "",
    target_type: str = "",
    target_id: str = "",
    external_userid: str = "",
    owner_userid: str = "",
    trace_id: str = "",
    idempotency_key: str = "",
    source_module: str = "",
    source_route: str = "",
    created_from: str = "",
    created_to: str = "",
) -> dict[str, Any]:
    payload = build_stats_payload(locals(), repository=PushCenterRepository())
    payload["runtime_queue"] = _runtime_queue_summary()
    payload["capability_owner"] = CAPABILITY_OWNER
    return payload


@router.post("/api/admin/push-center/jobs/{job_id}/retry")
async def push_center_retry_job(job_id: int, request: Request) -> JSONResponse:
    payload = await _payload(request)
    token_error = _action_or_internal_token_error(request, payload)
    if token_error:
        return _json({"ok": False, "error": token_error}, status_code=401)
    try:
        command = parse_manual_queue_command(
            payload,
            authenticated_actor=authenticated_queue_actor(request),
        )
    except QueueCommandPayloadError as exc:
        return _command_payload_error(exc)
    service = _queue_command_service(request)
    target = await run_in_threadpool(
        service.read_external_effect_target,
        int(job_id),
    )
    if target is None:
        return _json({"ok": False, "error": "push_center_job_not_found"}, status_code=404)
    return await _accepted_action_response(
        service,
        target,
        command,
        action="retry",
        source_route="/api/admin/push-center/jobs/{job_id}/retry",
    )


@router.post("/api/admin/push-center/jobs/{job_id}/cancel")
async def push_center_cancel_job(job_id: int, request: Request) -> JSONResponse:
    payload = await _payload(request)
    token_error = _action_or_internal_token_error(request, payload)
    if token_error:
        return _json({"ok": False, "error": token_error}, status_code=401)
    try:
        command = parse_manual_queue_command(
            payload,
            authenticated_actor=authenticated_queue_actor(request),
        )
    except QueueCommandPayloadError as exc:
        return _command_payload_error(exc)
    service = _queue_command_service(request)
    target = await run_in_threadpool(
        service.read_external_effect_target,
        int(job_id),
    )
    if target is None:
        return _json({"ok": False, "error": "push_center_job_not_found"}, status_code=404)
    return await _accepted_action_response(
        service,
        target,
        command,
        action="cancel",
        source_route="/api/admin/push-center/jobs/{job_id}/cancel",
    )
