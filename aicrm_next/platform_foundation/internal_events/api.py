from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from aicrm_next.shared.admin_action_runtime import ensure_admin_action_token, validate_admin_action_token
from aicrm_next.admin_shell import admin_path_for, shell_context
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
from aicrm_next.platform_foundation.execution_runtime.read_model import ExecutionRuntimeReadModel

from .config import diagnostics_payload as config_diagnostics_payload, worker_batch_size
from .repository import build_internal_event_repository
from .service import InternalEventService
from .view_model import build_diagnostics_payload, build_event_detail_payload, build_events_payload
from .worker import InternalEventWorker

router = APIRouter()
ROUTE_OWNER = "ai_crm_next"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_FRONTEND_COMPAT_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=[_TEMPLATES_DIR, _FRONTEND_COMPAT_TEMPLATES_DIR])
_INTERNAL_LANES = frozenset({"internal_general", "internal_financial"})


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _json(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    payload.setdefault("route_owner", ROUTE_OWNER)
    payload.setdefault("real_external_call_executed", False)
    headers = {
        "X-AICRM-Route-Owner": ROUTE_OWNER,
        "X-AICRM-Real-External-Call-Executed": "true" if bool(payload.get("real_external_call_executed")) else "false",
    }
    return JSONResponse(_json_safe(payload), status_code=status_code, headers=headers)


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


def _action_or_internal_token_error(request: Request, payload: dict[str, Any]) -> str:
    token = _text(request.headers.get("X-Admin-Action-Token")) or _text(payload.get("admin_action_token"))
    return validate_admin_action_token(token, request=request)


def _authenticated_actor(request: Request) -> str:
    return _text(authenticated_queue_actor(request))


def _runtime_queue_summary() -> dict[str, Any]:
    try:
        return ExecutionRuntimeReadModel().lane_summary(_INTERNAL_LANES)
    except Exception:
        return {}


def _service() -> InternalEventService:
    return InternalEventService(build_internal_event_repository())


def _queue_command_service(request: Request) -> QueueRuntimeCommandService:
    service = getattr(request.app.state, "queue_runtime_command_service", None)
    return service if service is not None else QueueRuntimeCommandService()


def _csv(value: Any) -> list[str] | None:
    if isinstance(value, list):
        items = [_text(item) for item in value if _text(item)]
    else:
        items = [_text(item) for item in _text(value).split(",") if _text(item)]
    return items or None


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
        },
        status_code=422,
    )


async def _preview_internal_due(payload: dict[str, Any]) -> dict[str, Any]:
    return await run_in_threadpool(
        InternalEventWorker(build_internal_event_repository()).preview_due,
        batch_size=_int(payload.get("batch_size") or payload.get("limit"), default=worker_batch_size(), minimum=1),
        event_types=_csv(payload.get("event_types")),
        consumer_names=_csv(payload.get("consumer_names")),
    )


async def _preview_internal_consumer(
    event_id: str,
    consumer_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await run_in_threadpool(
        InternalEventWorker(build_internal_event_repository()).dispatch_one_consumer,
        event_id,
        consumer_name,
        dry_run=True,
        force=_bool(payload.get("force"), default=False),
        reason=_text(payload.get("reason")),
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
    except QueueCommandConflict:
        return _json({"ok": False, "error": "queue_command_cas_conflict"}, status_code=409)
    except ValueError:
        return _json({"ok": False, "error": "queue_command_target_not_eligible"}, status_code=409)
    return _json(accepted_queue_command_payload(result, command), status_code=202)


def _page_context(request: Request) -> dict[str, Any]:
    context = shell_context(
        request=request,
        page_title="事件中心",
        page_summary="查看内部业务事实和每个消费者的执行状态。",
        active_endpoint="api.admin_internal_events_page",
    )
    context.update(
        {
            "breadcrumbs": [{"label": "客户管理后台", "href": "/"}, {"label": "事件中心", "href": ""}],
            "page_actions": [
                {"label": "刷新", "href": "#refresh", "variant": "secondary"},
                {"label": "导出当前页", "href": "#export", "variant": "secondary"},
            ],
            "operator_actor": _authenticated_actor(request),
            "admin_action_token": ensure_admin_action_token(),
            "url_for": admin_path_for,
        }
    )
    return context


@router.get("/admin/internal-events", name="api.admin_internal_events_page", response_class=HTMLResponse)
def admin_internal_events_page(request: Request):
    selected_event_id = _text(request.query_params.get("event_id"))
    if selected_event_id:
        encoded = quote(selected_event_id, safe="")
        return RedirectResponse(f"/admin/internal-events/{encoded}", status_code=303)
    return templates.TemplateResponse(request, "admin_console/internal_events.html", _page_context(request))


@router.get(
    "/admin/internal-events/{event_id}",
    name="api.admin_internal_event_page",
    response_class=HTMLResponse,
)
def admin_internal_event_page(event_id: str, request: Request):
    context = shell_context(
        request=request,
        page_title="事件详情",
        page_summary="查看单个业务事实、消费者执行与下游对账信息。",
        active_endpoint="api.admin_internal_events_page",
    )
    context.update(
        {
            "breadcrumbs": [
                {"label": "客户管理后台", "href": "/"},
                {"label": "事件中心", "href": "/admin/internal-events"},
                {"label": _text(event_id), "href": ""},
            ],
            "event_id": _text(event_id),
            "operator_actor": _authenticated_actor(request),
            "admin_action_token": ensure_admin_action_token(),
            "url_for": admin_path_for,
        }
    )
    return templates.TemplateResponse(request, "admin_console/internal_event_detail.html", context)


@router.get("/api/admin/internal-events")
def list_internal_events(
    event_section: str = "",
    event_type: str = "",
    aggregate_type: str = "",
    aggregate_id: str = "",
    subject_type: str = "",
    subject_id: str = "",
    consumer_name: str = "",
    consumer_status: str = "",
    trace_id: str = "",
    trace_hash: str = "",
    original_trace_hash: str = "",
    source_module: str = "",
    created_from: str = "",
    created_to: str = "",
    idempotency_key: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    payload = build_events_payload(locals(), repository=build_internal_event_repository())
    runtime_queue = _runtime_queue_summary()
    payload["runtime_queue"] = runtime_queue
    if runtime_queue and isinstance(payload.get("counts"), dict):
        payload["counts"]["due"] = int(runtime_queue.get("eligible") or 0)
        payload["counts"]["raw_open"] = int(runtime_queue.get("raw_open") or 0)
        payload["counts"]["held"] = int(runtime_queue.get("held") or 0)
        payload["counts"]["scheduled"] = int(runtime_queue.get("scheduled") or 0)
        payload["counts"]["retry_wait"] = int(runtime_queue.get("retry_wait") or 0)
        payload["counts"]["rate_limited"] = int(runtime_queue.get("rate_limited") or 0)
        payload["counts"]["in_flight"] = int(runtime_queue.get("in_flight") or 0)
        payload["counts"]["unknown"] = int(runtime_queue.get("unknown") or 0)
        payload["counts"]["dlq"] = int(runtime_queue.get("dlq") or 0)
    return payload


@router.get("/api/admin/internal-events/diagnostics")
def internal_events_diagnostics(
    event_section: str = "",
    event_type: str = "",
    consumer_name: str = "",
    consumer_status: str = "",
    trace_hash: str = "",
    original_trace_hash: str = "",
) -> dict[str, Any]:
    payload = build_diagnostics_payload(locals(), service=_service())
    payload["config"] = config_diagnostics_payload()
    payload.update(config_diagnostics_payload())
    return payload


@router.post("/api/admin/internal-events/run-due/preview")
async def preview_internal_event_run_due(request: Request) -> JSONResponse:
    token_error = _action_or_internal_token_error(request, {})
    if token_error:
        return _json({"ok": False, "error": token_error}, status_code=401)
    payload = await _payload(request)
    result = await _preview_internal_due(payload)
    return _json(result)


@router.post("/api/admin/internal-events/run-due")
async def run_internal_event_due(request: Request) -> JSONResponse:
    token_error = _action_or_internal_token_error(request, {})
    if token_error:
        return _json({"ok": False, "error": token_error}, status_code=401)
    payload = await _payload(request)
    if _bool(payload.get("dry_run"), default=True):
        return _json(await _preview_internal_due(payload))
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
            },
            status_code=422,
        )
    service = _queue_command_service(request)
    target = await run_in_threadpool(
        service.read_internal_due_target,
        item_id,
        event_types=_csv(payload.get("event_types")),
        consumer_names=_csv(payload.get("consumer_names")),
    )
    if target is None:
        return _json({"ok": False, "error": "internal_event_consumer_run_not_found"}, status_code=404)
    try:
        result = await run_in_threadpool(
            submit_manual_queue_command,
            service,
            target,
            command,
            source_route="/api/admin/internal-events/run-due",
        )
    except QueueCommandConflict:
        return _json({"ok": False, "error": "queue_command_cas_conflict"}, status_code=409)
    except ValueError:
        return _json({"ok": False, "error": "queue_command_target_not_eligible"}, status_code=409)
    return _json(accepted_queue_command_payload(result, command), status_code=202)


@router.get("/api/admin/internal-events/{event_id}")
def get_internal_event(event_id: str) -> JSONResponse:
    payload = build_event_detail_payload(event_id, service=_service())
    if not payload:
        return _json({"ok": False, "error": "internal_event_not_found"}, status_code=404)
    return _json(payload)


@router.get("/api/admin/internal-events/{event_id}/reconciliation")
def get_internal_event_reconciliation(event_id: str) -> JSONResponse:
    service = _service()
    if not service.get_event(event_id):
        return _json({"ok": False, "error": "internal_event_not_found"}, status_code=404)
    return _json(
        {
            "ok": True,
            "reconciliation": service.get_event_reconciliation(event_id),
            "route_owner": ROUTE_OWNER,
            "real_external_call_executed": False,
        }
    )


@router.post("/api/admin/internal-events/{event_id}/consumers/{consumer_name}/run")
async def run_internal_event_consumer(event_id: str, consumer_name: str, request: Request) -> JSONResponse:
    payload = await _payload(request)
    token_error = _action_or_internal_token_error(request, payload)
    if token_error:
        return _json({"ok": False, "error": token_error}, status_code=401)
    if _bool(payload.get("dry_run"), default=True):
        result = await _preview_internal_consumer(event_id, consumer_name, payload)
        if result.get("ok"):
            return _json(result)
        error = _text(result.get("error"))
        status_code = 404 if error in {"consumer_run_not_found", "internal_event_not_found"} else 409
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
        service.read_internal_consumer_target,
        event_id,
        consumer_name,
    )
    if target is None:
        return _json({"ok": False, "error": "consumer_run_not_found"}, status_code=404)
    return await _accepted_command_response(
        service,
        target,
        command,
        source_route="/api/admin/internal-events/{event_id}/consumers/{consumer_name}/run",
    )


@router.post("/api/admin/internal-events/{event_id}/consumers/{consumer_name}/retry")
async def retry_internal_event_consumer(event_id: str, consumer_name: str, request: Request) -> JSONResponse:
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
        service.read_internal_consumer_target,
        event_id,
        consumer_name,
    )
    if target is None:
        return _json({"ok": False, "error": "consumer_run_not_found"}, status_code=404)
    return await _accepted_action_response(
        service,
        target,
        command,
        action="retry",
        source_route="/api/admin/internal-events/{event_id}/consumers/{consumer_name}/retry",
    )


@router.post("/api/admin/internal-events/{event_id}/consumers/{consumer_name}/skip")
async def skip_internal_event_consumer(event_id: str, consumer_name: str, request: Request) -> JSONResponse:
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
        service.read_internal_consumer_target,
        event_id,
        consumer_name,
    )
    if target is None:
        return _json({"ok": False, "error": "consumer_run_not_found"}, status_code=404)
    return await _accepted_action_response(
        service,
        target,
        command,
        action="skip",
        source_route="/api/admin/internal-events/{event_id}/consumers/{consumer_name}/skip",
    )
