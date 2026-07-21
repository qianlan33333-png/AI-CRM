from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from aicrm_next.shared.admin_action_runtime import ensure_admin_action_token, validate_admin_action_token
from aicrm_next.admin_shell import admin_path_for, shell_context
from aicrm_next.platform_foundation.auth_platform.context import AuthContext
from aicrm_next.platform_foundation.external_effects import ExternalEffectService
from aicrm_next.platform_foundation.internal_events import InternalEventService
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
    QueueRuntimeCommandService,
)

from .repository import build_webhook_inbox_repository
from .service import WebhookInboxService

router = APIRouter()
ROUTE_OWNER = "ai_crm_next"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_FRONTEND_COMPAT_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=[_TEMPLATES_DIR, _FRONTEND_COMPAT_TEMPLATES_DIR])


def _text(value: Any) -> str:
    return str(value or "").strip()


def _authenticated_actor(request: Request) -> str:
    context = getattr(request.state, "auth_context", None)
    return _text(context.sub) if isinstance(context, AuthContext) else ""


def _int(value: Any, *, default: int, minimum: int = 0, maximum: int = 200) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bool(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def _payload(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except Exception:
        return {}
    return dict(raw or {}) if isinstance(raw, dict) else {}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if hasattr(value, "obj") and value.__class__.__module__.startswith("psycopg.types.json"):
        return _json_safe(value.obj)
    return value


def _json(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    payload.setdefault("route_owner", ROUTE_OWNER)
    payload.setdefault("real_external_call_executed", False)
    headers = {
        "X-AICRM-Route-Owner": ROUTE_OWNER,
        "X-AICRM-Real-External-Call-Executed": "true" if bool(payload.get("real_external_call_executed")) else "false",
    }
    return JSONResponse(_json_safe(payload), status_code=status_code, headers=headers)


def _action_or_internal_token_error(request: Request, payload: dict[str, Any]) -> str:
    token = _text(request.headers.get("X-Admin-Action-Token")) or _text(payload.get("admin_action_token"))
    return validate_admin_action_token(token, request=request)


def _repo():
    return build_webhook_inbox_repository()


def _queue_command_service(request: Request) -> QueueRuntimeCommandService:
    service = getattr(request.app.state, "queue_runtime_command_service", None)
    return service if service is not None else QueueRuntimeCommandService()


def _worker(request: Request, repository: Any) -> Any:
    factory = getattr(request.app.state, "wecom_callback_inbox_worker_factory", None)
    if not callable(factory):
        raise RuntimeError("WeCom callback inbox worker composition is unavailable")
    return factory(repository)


def _command_payload_error(exc: QueueCommandPayloadError) -> JSONResponse:
    return _json(
        {
            "ok": False,
            "error": "manual_queue_command_fields_required",
            "missing_fields": list(exc.missing_fields),
        },
        status_code=422,
    )


async def _preview_webhook_item(
    request: Request,
    repository: Any,
    inbox_id: int,
    *,
    reason: str,
) -> dict[str, Any]:
    return await run_in_threadpool(
        _worker(request, repository).dispatch_one,
        int(inbox_id),
        dry_run=True,
        reason=reason,
    )


async def _preview_webhook_due(
    request: Request,
    repository: Any,
    *,
    limit: int,
) -> dict[str, Any]:
    return await run_in_threadpool(
        _worker(request, repository).preview_due,
        limit=limit,
    )


async def _accepted_command_response(
    service: QueueRuntimeCommandService,
    target: Any,
    command: Any,
    *,
    source_route: str,
) -> JSONResponse:
    try:
        result = await run_in_threadpool(
            submit_manual_queue_command,
            service,
            target,
            command,
            source_route=source_route,
        )
    except QueueCommandConflict:
        return _json({"ok": False, "error": "queue_command_cas_conflict"}, status_code=409)
    except ValueError:
        return _json({"ok": False, "error": "queue_command_target_not_eligible"}, status_code=409)
    return _json(accepted_queue_command_payload(result, command), status_code=202)


async def _accepted_manual_action_response(
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
    except QueueCommandConflict:
        return _json({"ok": False, "error": "queue_command_cas_conflict"}, status_code=409)
    except ValueError:
        return _json({"ok": False, "error": "queue_command_target_not_eligible"}, status_code=409)
    return _json(accepted_queue_command_payload(result, command), status_code=202)


def _filters(**kwargs: Any) -> dict[str, Any]:
    return {key: _text(value) for key, value in kwargs.items() if _text(value)}


def _item_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "execution_id": _text(row.get("execution_id")),
        "parent_execution_id": _text(row.get("parent_execution_id")),
        "lane": _text(row.get("lane")),
        "available_at": row.get("available_at"),
        "hold_reason": _text(row.get("hold_reason")),
        "policy_version": _text(row.get("policy_version")),
        "row_version": _text(row.get("row_version")),
        "provider": _text(row.get("provider")),
        "event_family": _text(row.get("event_family")),
        "route": _text(row.get("route")),
        "method": _text(row.get("method")),
        "tenant_id": _text(row.get("tenant_id")),
        "corp_id": _text(row.get("corp_id")),
        "event_type": _text(row.get("event_type")),
        "change_type": _text(row.get("change_type")),
        "external_event_id": _text(row.get("external_event_id")),
        "idempotency_key": _text(row.get("idempotency_key")),
        "payload_summary_json": row.get("payload_summary_json") or {},
        "processing_summary_json": row.get("processing_summary_json") or {},
        "status": _text(row.get("status")),
        "attempt_count": int(row.get("attempt_count") or 0),
        "max_attempts": int(row.get("max_attempts") or 0),
        "next_retry_at": row.get("next_retry_at"),
        "locked_at": row.get("locked_at"),
        "locked_by": _text(row.get("locked_by")),
        "last_error_code": _text(row.get("last_error_code")),
        "last_error_message": _text(row.get("last_error_message")),
        "received_at": row.get("received_at"),
        "last_seen_at": row.get("last_seen_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "duplicate_count": int(row.get("duplicate_count") or 0),
    }


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return dict(value or {}) if isinstance(value, dict) else {}


def _safe_int_list(values: Any) -> list[int]:
    result: list[int] = []
    for value in values if isinstance(values, list) else []:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0 and parsed not in result:
            result.append(parsed)
    return result


def _processing_chain(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("processing_summary_json") or {}
    summary = dict(summary) if isinstance(summary, dict) else {}
    event_log_id = int(summary.get("event_log_id") or 0)
    internal_event_id = _text(summary.get("internal_event_id"))

    internal_events: list[dict[str, Any]] = []
    consumer_runs: list[dict[str, Any]] = []
    consumer_attempts: list[dict[str, Any]] = []
    if internal_event_id:
        internal_service = InternalEventService()
        event = internal_service.get_event(internal_event_id)
        if event:
            internal_events.append(event.to_dict())
        runs, _ = internal_service.list_consumer_runs({"event_id": internal_event_id}, limit=50)
        consumer_runs = [run.to_dict() for run in runs]
        consumer_attempts = [attempt.to_dict() for attempt in internal_service.list_attempts(event_id=internal_event_id)]

    effect_service = ExternalEffectService()
    job_ids = _safe_int_list(summary.get("external_effect_job_ids"))
    effect_jobs: list[dict[str, Any]] = []
    seen_job_ids: set[int] = set()
    for job_id in job_ids:
        job = effect_service.get(job_id)
        if job:
            job_payload = _as_dict(job)
            payload_id = int(job_payload.get("id") or 0)
            if payload_id > 0:
                seen_job_ids.add(payload_id)
            effect_jobs.append(job_payload)
    if event_log_id:
        jobs, _ = effect_service.list_jobs({"source_event_id": str(event_log_id), "business_type": "channel_entry"}, limit=50)
        for job in jobs:
            job_payload = _as_dict(job)
            payload_id = int(job_payload.get("id") or 0)
            if payload_id > 0 and payload_id not in seen_job_ids:
                seen_job_ids.add(payload_id)
                effect_jobs.append(job_payload)
    effect_attempts = [
        {
            "job_id": int(job.get("id") or 0),
            "attempts": [attempt.to_dict() for attempt in effect_service.list_attempts(int(job.get("id") or 0))],
        }
        for job in effect_jobs
        if int(job.get("id") or 0) > 0
    ]
    return {
        "webhook_inbox_id": int(row.get("id") or 0),
        "event_log_id": event_log_id,
        "processing_summary_json": summary,
        "internal_events": internal_events,
        "internal_event_consumer_runs": consumer_runs,
        "internal_event_consumer_attempts": consumer_attempts,
        "external_effect_jobs": effect_jobs,
        "external_effect_attempts": effect_attempts,
    }


def _page_context(request: Request) -> dict[str, Any]:
    context = shell_context(
        request=request,
        page_title="Webhook Inbox",
        page_summary="查看入站回调队列、失败重试、死信与企微回调链路。",
        active_endpoint="api.admin_webhook_inbox_page",
    )
    context.update(
        {
            "breadcrumbs": [{"label": "客户管理后台", "href": "/"}, {"label": "Webhook Inbox", "href": ""}],
            "page_actions": [
                {"label": "刷新", "href": "#refresh", "variant": "secondary"},
                {"label": "Dry-run", "href": "#run-due-preview", "variant": "secondary"},
            ],
            "admin_action_token": ensure_admin_action_token(),
            "operator_actor": _authenticated_actor(request),
            "url_for": admin_path_for,
        }
    )
    return context


@router.get("/admin/webhook-inbox", name="api.admin_webhook_inbox_page", response_class=HTMLResponse)
def admin_webhook_inbox_page(request: Request):
    return templates.TemplateResponse(request, "admin_console/webhook_inbox.html", _page_context(request))


@router.get("/api/admin/webhook-inbox/metrics")
def webhook_inbox_metrics(
    provider: str = "",
    event_family: str = "",
    route: str = "",
    status: str = "",
    received_from: str = "",
    received_to: str = "",
    tenant_id: str = "",
) -> JSONResponse:
    repo = _repo()
    metrics = WebhookInboxService(repo).queue_metrics(
        _filters(
            provider=provider,
            event_family=event_family,
            route=route,
            status=status,
            received_from=received_from,
            received_to=received_to,
            tenant_id=tenant_id,
        )
    )
    return _json({"ok": True, "queue_metrics": metrics.__dict__})


@router.get("/api/admin/webhook-inbox/items")
def list_webhook_inbox_items(
    provider: str = "",
    event_family: str = "",
    route: str = "",
    status: str = "",
    event_type: str = "",
    change_type: str = "",
    received_from: str = "",
    received_to: str = "",
    tenant_id: str = "",
    limit: int = 50,
    offset: int = 0,
) -> JSONResponse:
    repo = _repo()
    items = repo.list_items(
        _filters(
            provider=provider,
            event_family=event_family,
            route=route,
            status=status,
            event_type=event_type,
            change_type=change_type,
            received_from=received_from,
            received_to=received_to,
            tenant_id=tenant_id,
        ),
        limit=_int(limit, default=50, minimum=1),
        offset=_int(offset, default=0, minimum=0, maximum=100000),
    )
    return _json({"ok": True, "items": [_item_payload(row) for row in items], "limit": limit, "offset": offset})


@router.get("/api/admin/webhook-inbox/{inbox_id}")
def get_webhook_inbox_item(inbox_id: int) -> JSONResponse:
    row = _repo().get_item(int(inbox_id))
    if not row:
        return _json({"ok": False, "error": "webhook_inbox_item_not_found"}, status_code=404)
    return _json({"ok": True, "item": _item_payload(row), "processing_chain": _processing_chain(row)})


@router.post("/api/admin/webhook-inbox/{inbox_id}/retry")
async def retry_webhook_inbox_item(inbox_id: int, request: Request) -> JSONResponse:
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
        service.read_webhook_inbox_target,
        int(inbox_id),
        provider="wecom",
    )
    if target is None:
        return _json({"ok": False, "error": "webhook_inbox_item_not_found"}, status_code=404)
    return await _accepted_manual_action_response(
        service,
        target,
        command,
        action="retry",
        source_route="/api/admin/webhook-inbox/{inbox_id}/retry",
    )


@router.post("/api/admin/webhook-inbox/{inbox_id}/skip")
async def skip_webhook_inbox_item(inbox_id: int, request: Request) -> JSONResponse:
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
        service.read_webhook_inbox_target,
        int(inbox_id),
        provider="wecom",
    )
    if target is None:
        return _json({"ok": False, "error": "webhook_inbox_item_not_found"}, status_code=404)
    return await _accepted_manual_action_response(
        service,
        target,
        command,
        action="skip",
        source_route="/api/admin/webhook-inbox/{inbox_id}/skip",
    )


@router.post("/api/admin/webhook-inbox/{inbox_id}/dispatch")
async def dispatch_webhook_inbox_item(inbox_id: int, request: Request) -> JSONResponse:
    payload = await _payload(request)
    token_error = _action_or_internal_token_error(request, payload)
    if token_error:
        return _json({"ok": False, "error": token_error}, status_code=401)
    repo = _repo()
    dry_run = _bool(payload.get("dry_run"), default=True)
    if dry_run:
        result = await _preview_webhook_item(
            request,
            repo,
            int(inbox_id),
            reason=_text(payload.get("reason")) or "admin_dispatch_one",
        )
        status_code = 200 if result.get("ok") else (404 if result.get("status") == "not_found" else 409)
        result["route_owner"] = ROUTE_OWNER
        result["real_external_call_executed"] = False
        return _json(result, status_code=status_code)
    try:
        command = parse_manual_queue_command(
            payload,
            authenticated_actor=authenticated_queue_actor(request),
        )
    except QueueCommandPayloadError as exc:
        return _command_payload_error(exc)
    service = _queue_command_service(request)
    target = await run_in_threadpool(
        service.read_webhook_inbox_target,
        int(inbox_id),
        provider="wecom",
    )
    if target is None:
        return _json({"ok": False, "error": "webhook_inbox_item_not_found"}, status_code=404)
    return await _accepted_command_response(
        service,
        target,
        command,
        source_route="/api/admin/webhook-inbox/{inbox_id}/dispatch",
    )


@router.post("/api/admin/webhook-inbox/run-due")
async def run_webhook_inbox_due(request: Request) -> JSONResponse:
    payload = await _payload(request)
    token_error = _action_or_internal_token_error(request, payload)
    if token_error:
        return _json({"ok": False, "error": token_error}, status_code=401)
    provider = _text(payload.get("provider")) or "wecom"
    if provider != "wecom":
        return _json({"ok": False, "error": "webhook_inbox_provider_not_supported"}, status_code=400)
    repo = _repo()
    dry_run = _bool(payload.get("dry_run"), default=True)
    if dry_run:
        result = await _preview_webhook_due(
            request,
            repo,
            limit=_int(payload.get("batch_size") or payload.get("limit"), default=20, minimum=1),
        )
        result["route_owner"] = ROUTE_OWNER
        result["real_external_call_executed"] = False
        return _json(result)
    try:
        command = parse_manual_queue_command(
            payload,
            authenticated_actor=authenticated_queue_actor(request),
        )
    except QueueCommandPayloadError as exc:
        return _command_payload_error(exc)
    try:
        item_id = int(payload.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    if item_id <= 0:
        return _json(
            {
                "ok": False,
                "error": "manual_queue_command_fields_required",
                "missing_fields": ["item_id"],
            },
            status_code=422,
        )
    service = _queue_command_service(request)
    target = await run_in_threadpool(
        service.read_webhook_inbox_target,
        item_id,
        provider=provider,
    )
    if target is None:
        return _json({"ok": False, "error": "webhook_inbox_item_not_found"}, status_code=404)
    try:
        result = await run_in_threadpool(
            submit_manual_queue_command,
            service,
            target,
            command,
            source_route="/api/admin/webhook-inbox/run-due",
        )
    except QueueCommandConflict:
        return _json({"ok": False, "error": "queue_command_cas_conflict"}, status_code=409)
    except ValueError:
        return _json({"ok": False, "error": "queue_command_target_not_eligible"}, status_code=409)
    return _json(accepted_queue_command_payload(result, command), status_code=202)


@router.get("/api/admin/wecom/callback/reconciliation")
def wecom_callback_reconciliation(
    limit: int = 20,
    status: str = "",
    received_from: str = "",
    received_to: str = "",
) -> JSONResponse:
    repo = _repo()
    filters = _filters(
        provider="wecom",
        event_family="external_contact",
        status=status,
        received_from=received_from,
        received_to=received_to,
    )
    metrics = WebhookInboxService(repo).queue_metrics(filters)
    items = repo.list_items(filters, limit=_int(limit, default=20, minimum=1), offset=0)
    return _json(
        {
            "ok": True,
            "provider": "wecom",
            "event_family": "external_contact",
            "queue_metrics": metrics.__dict__,
            "recent_items": [_item_payload(row) for row in items],
        }
    )
