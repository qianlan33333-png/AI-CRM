from __future__ import annotations

import base64
import json
import os
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.admin_jobs_archive_sync_gateway import execute_archive_sync

from .application import (
    ListExternalChatRecordsQuery,
    ListArchivedMessagesQuery,
    SearchArchivedMessagesQuery,
    blocked_messages_side_effect,
    deprecated_messages_route,
)
from .sync_service import archive_health_payload

router = APIRouter()
_EXTERNAL_CHAT_SOURCE_STATUS = "external_chat_records"
_EXTERNAL_CHAT_PAGE_SIZE = 20


def _response(payload: dict) -> JSONResponse:
    status_code = int(payload.pop("status_code", 200) or 200)
    return JSONResponse(jsonable_encoder(payload), status_code=status_code)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _external_error(*, error_code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error_code": error_code,
            "message": message,
            "route_owner": "ai_crm_next",
            "source_status": _EXTERNAL_CHAT_SOURCE_STATUS,
            "fallback_used": False,
        },
        status_code=status_code,
    )


def _encode_cursor(offset: int | None) -> str:
    if offset is None:
        return ""
    payload = json.dumps({"offset": max(0, int(offset))}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    token = _text(cursor)
    if not token:
        return 0
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return max(0, int(payload.get("offset") or 0))
    except Exception as exc:
        raise ValueError("cursor is invalid") from exc


@router.get("/api/external/chat-records")
def list_external_chat_records(
    request: Request,
    mobile: str | None = Query(None, description="手机号"),
    unionid: str | None = Query(None, description="微信 unionid"),
    external_userid: str | None = Query(None, description="企业微信 external_userid"),
    start_time: int | None = Query(None, description="起始秒级 Unix 时间戳"),
    chat_scene: str = Query(..., description="private/group；也接受 私信/群聊"),
    with_userid: str | None = Query(None, description="私信场景下对话员工 userid，默认 HuangYouCan"),
    cursor: str | None = Query(None, description="下一页游标"),
) -> JSONResponse:
    try:
        offset = _decode_cursor(cursor)
        payload = ListExternalChatRecordsQuery()(
            mobile=mobile or "",
            unionid=unionid or "",
            external_userid=external_userid or "",
            start_time=start_time,
            chat_scene=chat_scene,
            with_userid=with_userid or "HuangYouCan",
            limit=_EXTERNAL_CHAT_PAGE_SIZE,
            offset=offset,
        )
    except ValueError as exc:
        return _external_error(error_code="invalid_request", message=str(exc), status_code=400)

    status_code = int(payload.pop("status_code", 200) or 200)
    if status_code != 200:
        return JSONResponse(jsonable_encoder(payload), status_code=status_code)

    items = list(payload.get("items") or [])
    total = int(payload.get("total") or 0)
    next_offset = offset + len(items) if offset + len(items) < total else None
    response_payload = {
        "ok": True,
        "items": items,
        "messages": items,
        "total": total,
        "count": len(items),
        "limit": _EXTERNAL_CHAT_PAGE_SIZE,
        "next_cursor": _encode_cursor(next_offset),
        "has_more": next_offset is not None,
        "external_userid": payload.get("external_userid") or "",
        "matched_by": payload.get("matched_by") or "",
        "filters": payload.get("filters") or {},
        "route_owner": "ai_crm_next",
        "source_status": _EXTERNAL_CHAT_SOURCE_STATUS,
        "read_model_status": payload.get("read_model_status") or "",
        "fallback_used": False,
    }
    return JSONResponse(jsonable_encoder(response_payload))


@router.get("/api/archive/health")
def archive_health(request: Request) -> JSONResponse:
    try:
        return JSONResponse(jsonable_encoder(archive_health_payload()))
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "error_code": "archive_health_failed",
                "message": str(exc),
                "route_owner": "ai_crm_next",
                "source_status": "next_archive_sync",
                "fallback_used": False,
            },
            status_code=502,
        )


@router.post("/api/archive/sync")
async def archive_sync(request: Request) -> JSONResponse:
    try:
        raw_payload = await request.json()
    except Exception:
        raw_payload = {}
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    if os.getenv("AICRM_ENABLE_IN_PROCESS_ARCHIVE_SYNC", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return JSONResponse(
            {
                "ok": False,
                "error_code": "in_process_archive_sync_disabled",
                "message": "Run scripts/run_incremental_archive_sync.py so the WeCom archive SDK stays outside the web process.",
                "route_owner": "ai_crm_next",
                "source_status": "next_archive_sync",
                "fallback_used": False,
                "runner": "scripts/run_incremental_archive_sync.py",
                "reply_monitor_skipped": True,
            },
            status_code=409,
        )
    try:
        result = execute_archive_sync(
            start_time=_text(payload.get("start_time")) or "2000-01-01 00:00:00",
            end_time=_text(payload.get("end_time")) or "2099-12-31 23:59:59",
            owner_userid=_text(payload.get("owner_userid")),
            cursor=_text(payload.get("cursor")),
            limit=int(payload.get("limit") or 100),
            max_pages=int(payload.get("max_pages") or 1000),
        )
        return JSONResponse(jsonable_encoder(result))
    except ValueError as exc:
        return _external_error(error_code="invalid_request", message=str(exc), status_code=400)
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "error_code": "archive_sync_failed",
                "message": str(exc),
                "route_owner": "ai_crm_next",
                "source_status": "next_archive_sync",
                "fallback_used": False,
                "reply_monitor_skipped": True,
            },
            status_code=502,
        )


@router.get("/api/messages/search")
def search_messages(
    external_userid: str | None = None,
    keyword: str | None = None,
    chat_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    payload = SearchArchivedMessagesQuery()(
        external_userid=external_userid or "",
        keyword=keyword or "",
        chat_type=chat_type or "",
        limit=limit,
        offset=offset,
    )
    return _response(payload)


@router.api_route("/api/messages/send", methods=["GET", "POST", "OPTIONS"])
def blocked_message_send():
    return _response(blocked_messages_side_effect(action="send"))


@router.api_route("/api/messages/broadcast", methods=["GET", "POST", "OPTIONS"])
def blocked_message_broadcast():
    return _response(blocked_messages_side_effect(action="broadcast"))


@router.api_route("/api/messages/archive/sync", methods=["GET", "POST", "OPTIONS"])
def blocked_message_archive_sync():
    return _response(blocked_messages_side_effect(action="archive_sync"))


@router.get("/api/messages/archive")
def deprecated_message_archive():
    return _response(deprecated_messages_route(replacement_route="/api/messages/search"))


@router.get("/api/messages/{external_userid}/archive")
def deprecated_external_message_archive(external_userid: str):
    return _response(deprecated_messages_route(replacement_route=f"/api/messages/{external_userid}"))


@router.get("/api/messages/{external_userid}/history")
def deprecated_external_message_history(external_userid: str):
    return _response(deprecated_messages_route(replacement_route=f"/api/messages/{external_userid}"))


@router.get("/api/messages/{external_userid}")
def list_messages(
    external_userid: str,
    chat_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    payload = ListArchivedMessagesQuery()(
        external_userid=external_userid,
        chat_type=chat_type or "",
        limit=limit,
        offset=offset,
    )
    return _response(payload)
