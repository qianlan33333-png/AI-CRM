from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aicrm_next.platform.shared.admin_action_runtime import validate_admin_action_token
from aicrm_next.platform.platform_foundation.auth_platform.context import AuthContext

from .application import (
    approve_broadcast_job,
    build_broadcast_job_detail_payload,
    build_broadcast_jobs_payload,
    build_jobs_archive_sync_payload,
    build_jobs_callbacks_payload,
    build_jobs_deferred_jobs_payload,
    build_jobs_message_batch_detail_payload,
    build_jobs_message_batches_payload,
    build_jobs_summary_payload,
    build_jobs_webhook_deliveries_payload,
    cancel_broadcast_job,
    execute_jobs_action,
)
from .domain import normalized_bool, normalized_text
from .notification_settings import (
    FeishuWebhookValidationError,
    get_feishu_notification_setting,
    enqueue_feishu_webhook_validation,
    send_broadcast_job_hourly_feishu_report,
    upsert_feishu_notification_setting,
)
router = APIRouter()


def _operator_from_request(request: Request, payload: dict[str, Any] | None = None, form: Any | None = None) -> str:
    del payload, form
    context = getattr(request.state, "auth_context", None)
    if isinstance(context, AuthContext) and normalized_text(context.sub):
        return normalized_text(context.sub)
    return "authenticated_admin"


def _manual_action_contract(payload: dict[str, Any]) -> str:
    if not normalized_text(payload.get("reason")):
        return "manual_action_reason_required"
    try:
        expected_version = int(payload.get("expected_version") or 0)
    except (TypeError, ValueError):
        expected_version = 0
    return "" if expected_version > 0 else "manual_action_expected_version_required"


def _jsonable(payload: Any) -> Any:
    return json.loads(json.dumps(payload, default=str, ensure_ascii=False))


async def _request_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
        return dict(payload or {}) if isinstance(payload, dict) else {}
    except Exception:
        return {}


async def _action_token_error(request: Request, payload: dict[str, Any] | None = None) -> str:
    token = normalized_text(request.headers.get("X-Admin-Action-Token")) or normalized_text((payload or {}).get("admin_action_token"))
    if not token:
        try:
            form = await request.form()
            token = normalized_text(form.get("admin_action_token"))
        except Exception:
            token = ""
    return validate_admin_action_token(token, request=request)


async def _cron_or_action_token_error(request: Request, payload: dict[str, Any] | None = None) -> str:
    return await _action_token_error(request, payload)


@router.get("/api/admin/jobs/summary")
def api_admin_jobs_summary(request: Request):
    return {"ok": True, "summary": _jsonable(build_jobs_summary_payload(dict(request.query_params)))}


@router.get("/api/admin/jobs/archive-sync")
def api_admin_jobs_archive_sync(request: Request):
    return {"ok": True, "archive_sync": _jsonable(build_jobs_archive_sync_payload(dict(request.query_params)))}


@router.post("/api/admin/jobs/archive-sync/run")
async def api_admin_jobs_archive_sync_run(request: Request):
    payload = await _request_payload(request)
    token_error = await _action_token_error(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    params = {
        "start_time": normalized_text(payload.get("start_time")),
        "end_time": normalized_text(payload.get("end_time")),
        "owner_userid": normalized_text(payload.get("owner_userid")),
        "cursor": normalized_text(payload.get("cursor")),
        "limit": normalized_text(payload.get("limit")),
        "max_pages": normalized_text(payload.get("max_pages")),
        "confirm": normalized_bool(payload.get("confirm")),
    }
    try:
        return _jsonable(execute_jobs_action(action="run-archive-sync", form=params, operator=_operator_from_request(request, payload)))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/admin/jobs/callbacks")
def api_admin_jobs_callbacks(request: Request):
    return {"ok": True, "callbacks": _jsonable(build_jobs_callbacks_payload(dict(request.query_params)))}


@router.get("/api/admin/jobs/message-batches")
def api_admin_jobs_message_batches(request: Request):
    return {"ok": True, "message_batches": _jsonable(build_jobs_message_batches_payload(dict(request.query_params)))}


@router.get("/api/admin/jobs/message-batches/{batch_id}")
def api_admin_jobs_message_batch_detail(batch_id: int, request: Request):
    payload = build_jobs_message_batch_detail_payload(batch_id, dict(request.query_params))
    if not payload.get("batch"):
        return JSONResponse({"ok": False, "error": "message batch not found"}, status_code=404)
    return {"ok": True, "message_batch": _jsonable(payload)}


@router.post("/api/admin/jobs/message-batches/{batch_id}/ack")
async def api_admin_jobs_message_batch_ack(batch_id: int, request: Request):
    payload = await _request_payload(request)
    token_error = await _action_token_error(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    params = {"batch_id": batch_id, "ack_note": normalized_text(payload.get("ack_note")), "confirm": normalized_bool(payload.get("confirm"))}
    try:
        return _jsonable(execute_jobs_action(action="ack-message-batch", form=params, operator=_operator_from_request(request, payload)))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/admin/jobs/deferred-jobs")
def api_admin_jobs_deferred_jobs(request: Request):
    return {"ok": True, "deferred_jobs": _jsonable(build_jobs_deferred_jobs_payload(dict(request.query_params)))}


@router.get("/api/admin/jobs/webhook-deliveries")
def api_admin_jobs_webhook_deliveries(request: Request):
    return {"ok": True, "webhook_deliveries": _jsonable(build_jobs_webhook_deliveries_payload(dict(request.query_params)))}


@router.get("/api/admin/broadcast-jobs")
def api_admin_broadcast_jobs(request: Request):
    payload = build_broadcast_jobs_payload(dict(request.query_params))
    return {
        "ok": True,
        "jobs": _jsonable(payload["jobs"]),
        "counts": payload["counts"],
        "filters": payload["filters"],
        "total": payload["total"],
        "status_options": payload["status_options"],
        "source_type_options": payload["source_type_options"],
        "route_owner": "ai_crm_next",
        "real_external_call_executed": False,
    }


def _with_ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **_jsonable(payload)}


@router.get("/api/admin/broadcast-jobs/notification-settings/feishu")
async def api_admin_broadcast_jobs_feishu_notification_settings(request: Request):
    del request
    return _with_ok(get_feishu_notification_setting())


@router.put("/api/admin/broadcast-jobs/notification-settings/feishu")
async def api_admin_broadcast_jobs_save_feishu_notification_settings(request: Request):
    payload = await _request_payload(request)
    token_error = await _action_token_error(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    try:
        setting = upsert_feishu_notification_setting(
            enabled=normalized_bool(payload.get("enabled")),
            webhook_url=normalized_text(payload.get("webhookUrl")),
            validation_status="unverified",
            validated_at=None,
            last_validation_error=None,
        )
        return _with_ok(setting)
    except FeishuWebhookValidationError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/admin/broadcast-jobs/notification-settings/feishu/validate")
async def api_admin_broadcast_jobs_validate_feishu_notification_settings(request: Request):
    payload = await _request_payload(request)
    token_error = await _action_token_error(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    try:
        result = enqueue_feishu_webhook_validation(
            webhook_url=normalized_text(payload.get("webhookUrl")),
            enabled=normalized_bool(payload.get("enabled")),
            actor_id=_operator_from_request(request),
        )
    except FeishuWebhookValidationError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(_jsonable(result), status_code=202)


@router.post("/api/admin/broadcast-jobs/feishu-hourly-report/run")
async def api_admin_broadcast_jobs_feishu_hourly_report_run(request: Request):
    payload = await _request_payload(request)
    token_error = await _cron_or_action_token_error(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    result = send_broadcast_job_hourly_feishu_report()
    return _jsonable({"ok": result.get("status") in {"queued", "sent"}, **result})


@router.get("/api/admin/broadcast-jobs/{job_id}")
def api_admin_broadcast_job_detail(job_id: int):
    payload = build_broadcast_job_detail_payload(job_id)
    if not payload:
        return JSONResponse(
            {
                "ok": False,
                "error": "broadcast_job_not_found",
                "route_owner": "ai_crm_next",
                "real_external_call_executed": False,
            },
            status_code=404,
        )
    return _jsonable(payload)


@router.post("/api/admin/jobs/order-identity-repair/run")
async def api_admin_jobs_order_identity_repair_run(request: Request):
    payload = await _request_payload(request)
    token_error = await _cron_or_action_token_error(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    return JSONResponse(
        {
            "ok": False,
            "error": "order_identity_repair_retired",
            "retired": True,
            "message": "wechat_pay_order_identity_repair has been retired; paid order identity is handled by the current order/customer identity projection path.",
            "replacement": "current_order_customer_identity_projection",
            "route_owner": "ai_crm_next",
            "real_external_call_executed": False,
        },
        status_code=410,
    )


@router.post("/api/admin/broadcast-jobs/{job_id}/approve")
async def api_admin_broadcast_jobs_approve(job_id: int, request: Request):
    payload = await _request_payload(request)
    token_error = await _action_token_error(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    contract_error = _manual_action_contract(payload)
    if contract_error:
        return JSONResponse({"ok": False, "error": contract_error}, status_code=422)
    try:
        return _jsonable(
            approve_broadcast_job(
                job_id,
                operator=_operator_from_request(request),
                reason=normalized_text(payload.get("reason")),
            )
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)


@router.post("/api/admin/broadcast-jobs/{job_id}/cancel")
async def api_admin_broadcast_jobs_cancel(job_id: int, request: Request):
    payload = await _request_payload(request)
    token_error = await _action_token_error(request, payload)
    if token_error:
        return JSONResponse({"ok": False, "error": token_error}, status_code=401)
    contract_error = _manual_action_contract(payload)
    if contract_error:
        return JSONResponse({"ok": False, "error": contract_error}, status_code=422)
    try:
        return _jsonable(
            cancel_broadcast_job(
                job_id,
                operator=_operator_from_request(request),
                reason=normalized_text(payload.get("reason")),
            )
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
