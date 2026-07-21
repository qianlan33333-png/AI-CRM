from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from aicrm_next.shared.admin_action_runtime import validate_admin_action_token
from aicrm_next.platform_foundation.execution_runtime.api_command import (
    QueueCommandPayloadError,
    accepted_queue_command_payload,
    authenticated_queue_actor,
    parse_manual_queue_command,
    submit_manual_queue_action,
    submit_manual_queue_command,
)
from aicrm_next.platform_foundation.execution_runtime.commands import (
    QueueCommandConflict,
    QueueCommandDuplicateRiskRequired,
    QueueRuntimeCommandService,
)

from .adapters import DEFAULT_ADAPTER_REGISTRY, ExternalEffectAdapterRegistry, wecom_execution_settings
from .repo import build_external_effect_repository
from .service import ExternalEffectService
from .test_receiver import create_loopback_job, record_test_receiver_request, safe_current_base_url
from .view_model import (
    build_external_effect_diagnostics_payload,
    build_external_effect_jobs_payload,
    build_troubleshooting_job_detail_payload,
    build_troubleshooting_jobs_payload,
    build_troubleshooting_summary_payload,
    external_effect_attempt_item,
    external_effect_filters,
    external_effect_job_detail_item,
    external_effect_receipt_item,
    redact_external_effect_admin_response,
    redact_external_effect_payload,
)
from .worker import ExternalEffectWorker

router = APIRouter()
ROUTE_OWNER = "ai_crm_next"


def _text(value: Any) -> str:
    return str(value or "").strip()


async def _payload(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except Exception:
        return {}
    return dict(raw or {}) if isinstance(raw, dict) else {}


def _bool(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Any, *, default: int, minimum: int = 0, maximum: int = 200) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _json(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    headers = {
        "X-AICRM-Route-Owner": ROUTE_OWNER,
        "X-AICRM-Real-External-Call-Executed": "true" if bool(payload.get("real_external_call_executed")) else "false",
    }
    return JSONResponse(payload, status_code=status_code, headers=headers)


def _action_or_internal_token_error(request: Request, payload: dict[str, Any]) -> str:
    token = _text(request.headers.get("X-Admin-Action-Token")) or _text(payload.get("admin_action_token"))
    return validate_admin_action_token(token, request=request)


def _service() -> ExternalEffectService:
    return ExternalEffectService(build_external_effect_repository())


def _queue_command_service(request: Request) -> QueueRuntimeCommandService:
    service = getattr(request.app.state, "queue_runtime_command_service", None)
    return service if service is not None else QueueRuntimeCommandService()


def _adapter_registry(request: Request) -> ExternalEffectAdapterRegistry:
    return getattr(request.app.state, "external_effect_adapter_registry", DEFAULT_ADAPTER_REGISTRY)


def _command_item_id(payload: dict[str, Any]) -> int:
    try:
        item_id = int(payload.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    return item_id if item_id > 0 else 0


def _command_payload_error(exc: QueueCommandPayloadError) -> JSONResponse:
    return _json(
        {
            "ok": False,
            "error": "manual_queue_command_fields_required",
            "missing_fields": list(exc.missing_fields),
            "route_owner": ROUTE_OWNER,
            "real_external_call_executed": False,
        },
        status_code=422,
    )


async def _preview_external_due(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    result = await run_in_threadpool(
        ExternalEffectWorker(build_external_effect_repository(), _adapter_registry(request)).preview_due,
        batch_size=_int(payload.get("batch_size") or payload.get("limit"), default=10, minimum=1),
        effect_types=[_text(item) for item in payload.get("effect_types") or [] if _text(item)] or None,
        test_only=_bool(payload.get("test_only"), default=False),
    )
    result["route_owner"] = ROUTE_OWNER
    return redact_external_effect_admin_response(result)


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
        return _json(
            {
                "ok": False,
                "error": "duplicate_risk_confirmation_required",
                "route_owner": ROUTE_OWNER,
                "real_external_call_executed": False,
            },
            status_code=409,
        )
    except QueueCommandConflict:
        return _json(
            {
                "ok": False,
                "error": "queue_command_cas_conflict",
                "route_owner": ROUTE_OWNER,
                "real_external_call_executed": False,
            },
            status_code=409,
        )
    except ValueError:
        return _json(
            {
                "ok": False,
                "error": "queue_command_target_not_eligible",
                "route_owner": ROUTE_OWNER,
                "real_external_call_executed": False,
            },
            status_code=409,
        )
    accepted = accepted_queue_command_payload(result, command)
    accepted["route_owner"] = ROUTE_OWNER
    return _json(accepted, status_code=202)


@router.get("/api/admin/external-effects/jobs")
def list_external_effect_jobs(
    effect_type: str = "",
    status: str = "",
    target_type: str = "",
    target_id: str = "",
    business_type: str = "",
    business_id: str = "",
    trace_id: str = "",
    job_id: int = 0,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return build_external_effect_jobs_payload(
        {
            "effect_type": effect_type,
            "status": status,
            "target_type": target_type,
            "target_id": target_id,
            "business_type": business_type,
            "business_id": business_id,
            "trace_id": trace_id,
            "job_id": job_id,
            "limit": limit,
            "offset": offset,
        },
        service=_service(),
    )


@router.get("/api/admin/external-effects/diagnostics")
def external_effect_diagnostics(
    request: Request,
    effect_type: str = "",
    status: str = "",
    target_type: str = "",
    target_id: str = "",
    business_type: str = "",
    business_id: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    return build_external_effect_diagnostics_payload(
        external_effect_filters(
            {
                "effect_type": effect_type,
                "status": status,
                "target_type": target_type,
                "target_id": target_id,
                "business_type": business_type,
                "business_id": business_id,
                "trace_id": trace_id,
            }
        ),
        service=_service(),
        current_base_url=safe_current_base_url(request),
    )


@router.get("/api/admin/wecom/execution-diagnostics")
def wecom_execution_diagnostics() -> dict[str, Any]:
    settings = wecom_execution_settings()
    return {
        "ok": True,
        "route_owner": ROUTE_OWNER,
        "real_external_call_executed": False,
        "execution_mode": settings["execution_mode"],
        "corp_id_present": settings["corp_id_present"],
        "contact_secret_present": settings["contact_secret_present"],
        "default_sender_userid_present": settings["default_sender_userid_present"],
        "enabled_effect_types": settings["enabled_effect_types"],
        "deprecated_settings_present": settings["deprecated_settings_present"],
        "blocking_reasons": settings["blocking_reasons"],
        "wecom_execution": settings,
    }


@router.get(
    "/api/admin/external-effects/troubleshooting/summary",
    summary="外部动作队列排障汇总",
    description="返回 External Effect Queue 的排障汇总、失败/阻断计数、队列指标和执行保护状态。不执行任何外部调用。",
)
def external_effect_troubleshooting_summary(
    effect_type: str = "",
    status: str = "",
    target_type: str = "",
    target_id: str = "",
    business_type: str = "",
    business_id: str = "",
    trace_id: str = "",
    last_error_code: str = "",
    idempotency_key: str = "",
    problem_only: str = "true",
) -> dict[str, Any]:
    return build_troubleshooting_summary_payload(locals(), service=_service())


@router.get(
    "/api/admin/external-effects/troubleshooting/jobs",
    summary="查询外部动作队列问题任务",
    description="按 effect_type、status、target、business、trace_id、last_error_code、idempotency_key 查询问题任务。默认只返回 failed/blocked/dispatching 或带 last_error 的任务，且不返回 payload_json。",
)
def list_external_effect_troubleshooting_jobs(
    effect_type: str = "",
    status: str = "",
    target_type: str = "",
    target_id: str = "",
    business_type: str = "",
    business_id: str = "",
    trace_id: str = "",
    last_error_code: str = "",
    idempotency_key: str = "",
    problem_only: str = "true",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return build_troubleshooting_jobs_payload(locals(), service=_service())


@router.get(
    "/api/admin/external-effects/troubleshooting/jobs/{job_id}",
    summary="查询外部动作队列问题任务详情",
    description="返回单个 external_effect_job 的脱敏排障详情和 external_effect_attempt 列表。不返回完整 payload_json。",
)
def get_external_effect_troubleshooting_job(job_id: int) -> JSONResponse:
    payload = build_troubleshooting_job_detail_payload(job_id, service=_service())
    if not payload:
        return _json({"ok": False, "error": "external_effect_job_not_found", "route_owner": ROUTE_OWNER}, status_code=404)
    return _json(payload)


@router.get("/api/admin/external-effects/jobs/{job_id}")
def get_external_effect_job(job_id: int) -> JSONResponse:
    service = _service()
    job = service.get(job_id)
    if not job:
        return _json({"ok": False, "error": "external_effect_job_not_found", "route_owner": ROUTE_OWNER}, status_code=404)
    return _json(
        {
            "ok": True,
            "job": external_effect_job_detail_item(job),
            "attempts": [external_effect_attempt_item(attempt) for attempt in service.list_attempts(job_id)],
            "route_owner": ROUTE_OWNER,
        }
    )


@router.post("/api/admin/external-effects/run-due/preview")
async def preview_external_effect_run_due(request: Request) -> JSONResponse:
    token_error = _action_or_internal_token_error(request, {})
    if token_error:
        return _json({"ok": False, "error": token_error, "route_owner": ROUTE_OWNER, "real_external_call_executed": False}, status_code=401)
    payload = await _payload(request)
    return _json(await _preview_external_due(request, payload))


@router.post("/api/admin/external-effects/run-due")
async def run_external_effect_due(request: Request) -> JSONResponse:
    token_error = _action_or_internal_token_error(request, {})
    if token_error:
        return _json({"ok": False, "error": token_error, "route_owner": ROUTE_OWNER, "real_external_call_executed": False}, status_code=401)
    payload = await _payload(request)
    if _bool(payload.get("dry_run"), default=True):
        return _json(await _preview_external_due(request, payload))
    try:
        command = parse_manual_queue_command(
            payload,
            authenticated_actor=authenticated_queue_actor(request),
        )
    except QueueCommandPayloadError as exc:
        return _command_payload_error(exc)
    item_id = _command_item_id(payload)
    if not item_id:
        return _json(
            {
                "ok": False,
                "error": "manual_queue_command_fields_required",
                "missing_fields": ["item_id"],
                "route_owner": ROUTE_OWNER,
                "real_external_call_executed": False,
            },
            status_code=422,
        )
    effect_types = [_text(item) for item in payload.get("effect_types") or [] if _text(item)] or None
    service = _queue_command_service(request)
    target = await run_in_threadpool(
        service.read_external_effect_target,
        item_id,
        effect_types=effect_types,
        test_only=_bool(payload.get("test_only"), default=False),
    )
    if target is None:
        return _json(
            {
                "ok": False,
                "error": "external_effect_job_not_found",
                "route_owner": ROUTE_OWNER,
                "real_external_call_executed": False,
            },
            status_code=404,
        )
    try:
        result = await run_in_threadpool(
            submit_manual_queue_command,
            service,
            target,
            command,
            source_route="/api/admin/external-effects/run-due",
        )
    except QueueCommandConflict:
        return _json(
            {
                "ok": False,
                "error": "queue_command_cas_conflict",
                "route_owner": ROUTE_OWNER,
                "real_external_call_executed": False,
            },
            status_code=409,
        )
    except ValueError:
        return _json(
            {
                "ok": False,
                "error": "queue_command_target_not_eligible",
                "route_owner": ROUTE_OWNER,
                "real_external_call_executed": False,
            },
            status_code=409,
        )
    accepted = accepted_queue_command_payload(result, command)
    accepted["route_owner"] = ROUTE_OWNER
    return _json(accepted, status_code=202)


@router.post("/api/external-effects/test-receiver")
async def external_effect_test_receiver(request: Request) -> JSONResponse:
    status_code, payload = await record_test_receiver_request(
        request=request,
        repository=build_external_effect_repository(),
    )
    return _json(payload, status_code=status_code)


@router.post("/api/admin/external-effects/test-loopback/jobs")
async def create_external_effect_test_loopback_job(request: Request) -> JSONResponse:
    payload = await _payload(request)
    token_error = _action_or_internal_token_error(request, payload)
    if token_error:
        return _json({"ok": False, "error": token_error, "route_owner": ROUTE_OWNER}, status_code=401)
    if _text(payload.get("webhook_url")) or _text(payload.get("target_url")):
        return _json({"ok": False, "error": "webhook_url_not_allowed", "route_owner": ROUTE_OWNER}, status_code=400)
    try:
        result = create_loopback_job(
            request=request,
            service=_service(),
            scenario=_text(payload.get("scenario")),
            response_status=int(payload.get("response_status")) if payload.get("response_status") not in (None, "") else None,
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc), "route_owner": ROUTE_OWNER}, status_code=400)
    result["route_owner"] = ROUTE_OWNER
    return _json(result)


@router.get("/api/admin/external-effects/test-receipts")
def list_external_effect_test_receipts(
    job_id: str = "",
    effect_type: str = "",
    trace_id: str = "",
    event_id: str = "",
    received_from: str = "",
    received_to: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    filters = {
        "job_id": job_id,
        "effect_type": effect_type,
        "trace_id": trace_id,
        "event_id": event_id,
        "received_from": received_from,
        "received_to": received_to,
    }
    items, total = _service().list_test_receipts(filters, limit=_int(limit, default=50, minimum=1), offset=_int(offset, default=0))
    return {
        "ok": True,
        "items": [external_effect_receipt_item(item) for item in items],
        "total": total,
        "filters": {key: redact_external_effect_payload(value, key=key) for key, value in filters.items() if _text(value)},
        "route_owner": ROUTE_OWNER,
    }


@router.get("/api/admin/external-effects/test-receipts/{receipt_id}")
def get_external_effect_test_receipt(receipt_id: str) -> JSONResponse:
    receipt = _service().get_test_receipt(receipt_id)
    if not receipt:
        return _json({"ok": False, "error": "external_effect_test_receipt_not_found", "route_owner": ROUTE_OWNER}, status_code=404)
    return _json({"ok": True, "receipt": external_effect_receipt_item(receipt), "route_owner": ROUTE_OWNER})


@router.post("/api/admin/external-effects/jobs/{job_id}/retry")
async def retry_external_effect_job(job_id: int, request: Request) -> JSONResponse:
    payload = await _payload(request)
    token_error = _action_or_internal_token_error(request, payload)
    if token_error:
        return _json({"ok": False, "error": token_error, "route_owner": ROUTE_OWNER}, status_code=401)
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
        return _json(
            {"ok": False, "error": "external_effect_job_not_found", "route_owner": ROUTE_OWNER},
            status_code=404,
        )
    return await _accepted_action_response(
        service,
        target,
        command,
        action="retry",
        source_route="/api/admin/external-effects/jobs/{job_id}/retry",
    )


@router.post("/api/admin/external-effects/jobs/{job_id}/cancel")
async def cancel_external_effect_job(job_id: int, request: Request) -> JSONResponse:
    payload = await _payload(request)
    token_error = _action_or_internal_token_error(request, payload)
    if token_error:
        return _json({"ok": False, "error": token_error, "route_owner": ROUTE_OWNER}, status_code=401)
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
        return _json(
            {"ok": False, "error": "external_effect_job_not_found", "route_owner": ROUTE_OWNER},
            status_code=404,
        )
    return await _accepted_action_response(
        service,
        target,
        command,
        action="cancel",
        source_route="/api/admin/external-effects/jobs/{job_id}/cancel",
    )
