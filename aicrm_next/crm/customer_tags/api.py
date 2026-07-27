from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from aicrm_next.shared.admin_read_fallback import admin_read_unavailable_payload

from .admin_write import (
    WeComTagWriteInputError,
    WeComTagWriteNotFoundError,
    WeComTagWriteProductionUnavailableError,
    execute_wecom_tag_write,
)
from .commands import (
    CreateWeComTagCommand,
    CreateWeComTagGroupCommand,
    DeleteWeComTagCommand,
    DeleteWeComTagGroupCommand,
    UpdateWeComTagCommand,
    UpdateWeComTagGroupCommand,
    WeComTagWriteCommand,
)
from .dto import LiveTagRequest
from .live_mutation import (
    WeComTagMutationInputError,
    execute_wecom_tag_mutation,
    tag_execution_status,
)
from .mutation_commands import PlanWeComTagMarkCommand, PlanWeComTagUnmarkCommand, WeComTagMutationCommand
from .read_model import build_tag_catalog_repository
from .sync_service import WeComTagSyncError, execute_wecom_tag_catalog_sync


router = APIRouter()
read_router = APIRouter()
write_router = APIRouter()


def _production_unavailable(exc: Exception) -> JSONResponse:
    payload = admin_read_unavailable_payload(
        capability_owner="aicrm_next/crm/customer_tags",
        page_error="当前未获取到企微标签，可手工填写 tag_id",
        exc=exc,
        items_keys=("groups", "tags", "items"),
        count_keys=("count", "total_tags"),
        extra={"sync_executed": False, "fixture_used": False, "tag_limit": 1000},
    )
    return JSONResponse(jsonable_encoder(payload), status_code=200)


def _read_catalog_payload() -> dict:
    catalog = build_tag_catalog_repository().list_catalog()
    return catalog.to_payload()


@read_router.get("/api/admin/wecom/tags")
def list_admin_wecom_tags_read_model():
    try:
        return _read_catalog_payload()
    except Exception as exc:
        return _production_unavailable(exc)


@read_router.get("/api/admin/wecom/tags/{tag_id}")
def get_admin_wecom_tag_read_model(tag_id: str):
    normalized = str(tag_id or "").strip()
    if normalized == "fake-stub":
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        payload = _read_catalog_payload()
    except Exception as exc:
        return _production_unavailable(exc)
    tags = [tag for tag in payload["tags"] if str(tag.get("tag_id") or "").strip() == normalized]
    groups = [
        group
        for group in payload["groups"]
        if tags and str(group.get("group_id") or "").strip() == str(tags[0].get("group_id") or "").strip()
    ]
    return {**payload, "items": tags, "tags": tags, "groups": groups, "count": len(tags), "total_tags": len(tags)}


@read_router.get("/api/admin/wecom/tag-groups")
def list_admin_wecom_tag_groups_read_model():
    try:
        payload = _read_catalog_payload()
    except Exception as exc:
        return _production_unavailable(exc)
    return {**payload, "items": payload["groups"], "count": len(payload["groups"])}


@read_router.get("/api/admin/wecom/tag-groups/{group_id}")
def get_admin_wecom_tag_group_read_model(group_id: str):
    try:
        payload = _read_catalog_payload()
    except Exception as exc:
        return _production_unavailable(exc)
    normalized = str(group_id or "").strip()
    groups = [group for group in payload["groups"] if str(group.get("group_id") or "").strip() == normalized]
    tags = [tag for tag in payload["tags"] if str(tag.get("group_id") or "").strip() == normalized]
    return {**payload, "items": tags, "groups": groups, "tags": tags, "count": len(tags), "total_tags": len(tags)}


@write_router.api_route("/api/admin/wecom/tags/sync", methods=["POST", "OPTIONS"])
@write_router.api_route("/api/admin/wecom/tags/sync-due", methods=["POST", "OPTIONS"])
async def sync_admin_wecom_tags_command(request: Request):
    if request.method == "OPTIONS":
        return Response(status_code=204)
    try:
        body = await _json_body(request)
        payload = execute_wecom_tag_catalog_sync(
            operator=str(body.get("operator") or body.get("actor_id") or request.headers.get("X-AICRM-Actor-Id") or "admin_wecom_tags_sync").strip()
        )
        return JSONResponse(jsonable_encoder(payload), status_code=200)
    except WeComTagSyncError as exc:
        return _write_error(
            str(exc),
            status_code=502,
            source_status="next_live_sync_failed",
            write_model_status="sync_failed",
            error_code="wecom_tag_sync_failed",
            degraded=True,
        )


@write_router.api_route("/api/admin/wecom/tag-groups", methods=["POST", "OPTIONS"])
async def create_admin_wecom_tag_group_command(request: Request):
    return await _execute_write(request, CreateWeComTagGroupCommand)


@write_router.api_route("/api/admin/wecom/tag-groups/{group_id}", methods=["PUT", "PATCH", "DELETE", "OPTIONS"])
async def mutate_admin_wecom_tag_group_command(request: Request, group_id: str):
    command_type = DeleteWeComTagGroupCommand if request.method == "DELETE" else UpdateWeComTagGroupCommand
    return await _execute_write(request, command_type, target_id=group_id)


@write_router.api_route("/api/admin/wecom/tags", methods=["POST", "OPTIONS"])
async def create_admin_wecom_tag_command(request: Request):
    return await _execute_write(request, CreateWeComTagCommand)


@write_router.api_route("/api/admin/wecom/tags/{tag_id}", methods=["PUT", "PATCH", "DELETE", "OPTIONS"])
async def mutate_admin_wecom_tag_command(request: Request, tag_id: str):
    command_type = DeleteWeComTagCommand if request.method == "DELETE" else UpdateWeComTagCommand
    return await _execute_write(request, command_type, target_id=tag_id)


async def _execute_write(request: Request, command_type: type[WeComTagWriteCommand], target_id: str = "") -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204)
    try:
        body = await _json_body(request)
        command = command_type(
            idempotency_key=str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or "").strip(),
            actor_id=str(body.get("actor_id") or request.headers.get("X-AICRM-Actor-Id") or "wecom_tag_admin"),
            actor_type=str(body.get("actor_type") or request.headers.get("X-AICRM-Actor-Type") or "user"),
            target_id=target_id,
            payload={
                key: value
                for key, value in body.items()
                if key not in {"actor_id", "actor_type", "idempotency_key", "dry_run", "trace_id", "command_id"}
            },
            dry_run=_as_bool(body.get("dry_run")),
            source_route=request.url.path,
            trace_id=str(body.get("trace_id") or request.headers.get("X-Request-Id") or uuid4().hex),
        )
        payload = execute_wecom_tag_write(command)
        return JSONResponse(jsonable_encoder(payload), status_code=200)
    except WeComTagWriteInputError as exc:
        return _write_error(str(exc), status_code=400, source_status="next_command", write_model_status="input_error", error_code="input_error")
    except WeComTagWriteNotFoundError as exc:
        return _write_error(str(exc), status_code=404, source_status="next_command", write_model_status="not_found", error_code="not_found")
    except WeComTagWriteProductionUnavailableError as exc:
        return _write_error(str(exc), status_code=503, source_status="production_unavailable", write_model_status="unavailable", error_code="production_unavailable", degraded=True)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise WeComTagWriteInputError("json object body is required")
    return payload


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _write_error(
    message: str,
    *,
    status_code: int,
    source_status: str,
    write_model_status: str,
    error_code: str,
    degraded: bool = False,
) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": message,
            "error_code": error_code,
            "source_status": source_status,
            "write_model_status": write_model_status,
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
            "sync_executed": False,
            "degraded": degraded,
        },
        status_code=status_code,
    )


@read_router.get("/api/admin/wecom/tags/live/gate")
def list_wecom_tags_live_gate() -> dict:
    return tag_execution_status()


@router.api_route("/api/admin/wecom/tags/live/mark", methods=["POST", "OPTIONS"])
async def mark_tags_live(request: Request) -> Response:
    return await _execute_live_mutation(request, PlanWeComTagMarkCommand)


@router.api_route("/api/admin/wecom/tags/live/unmark", methods=["POST", "OPTIONS"])
async def unmark_tags_live(request: Request) -> Response:
    return await _execute_live_mutation(request, PlanWeComTagUnmarkCommand)


async def _execute_live_mutation(request: Request, command_type: type[WeComTagMutationCommand]) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204)
    try:
        body = await _json_body(request)
        request_payload = LiveTagRequest(**body)
        command = command_type(
            idempotency_key=str(request.headers.get("Idempotency-Key") or request_payload.idempotency_key or "").strip(),
            actor_id=str(body.get("actor_id") or request_payload.operator or request.headers.get("X-AICRM-Actor-Id") or "wecom_tag_operator"),
            actor_type=str(body.get("actor_type") or request.headers.get("X-AICRM-Actor-Type") or "user"),
            external_userid=request_payload.external_userid,
            tag_ids=request_payload.tag_ids,
            source_route=request.url.path,
            source_context={
                "source": "admin_wecom_tags_live_mutation",
                "operator": request_payload.operator,
            },
            dry_run=_as_bool(body.get("dry_run")),
            trace_id=str(body.get("trace_id") or request.headers.get("X-Request-Id") or uuid4().hex),
        )
        payload = execute_wecom_tag_mutation(command)
        return JSONResponse(jsonable_encoder(payload), status_code=200)
    except (WeComTagMutationInputError, WeComTagWriteInputError) as exc:
        return JSONResponse(
            {
                "ok": False,
                "error": str(exc),
                "error_code": _live_mutation_error_code(str(exc)),
                "source_status": "next_command",
                "route_owner": "ai_crm_next",
                "fallback_used": False,
                "adapter_mode": "real_blocked",
                "real_external_call_executed": False,
                "wecom_api_called": False,
            },
            status_code=400,
        )


def _live_mutation_error_code(message: str) -> str:
    if "external_userid" in message:
        return "external_userid_missing"
    if "tag_ids" in message:
        return "tag_ids_missing"
    return "input_error"
