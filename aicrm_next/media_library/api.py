from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response

from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.safe_logging import safe_log_exception

from .application import (
    DeleteMediaItemCommand,
    EnsureGroupInviteBindingCommand,
    GetMediaItemQuery,
    GetImageThumbnailQuery,
    GetImageVariantQuery,
    ImportImageFromBase64Command,
    ImportImageFromUrlCommand,
    ListMediaFacetsQuery,
    ListMediaItemsQuery,
    TestResolveMiniprogramThumbCommand,
    UploadAttachmentCommand,
    UploadImageCommand,
    UpdateGroupInviteBindingCommand,
    UpsertMediaItemCommand,
)
from .dto import (
    AttachmentUpsertRequest,
    GroupInviteBindingEnsureRequest,
    GroupInviteBindingUpdateRequest,
    GroupInviteUpsertRequest,
    ImageFromBase64Request,
    ImageFromUrlRequest,
    ImageUpsertRequest,
    MiniprogramUpsertRequest,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _adapter_mode() -> str:
    storage = _visible_media_mode(os.getenv("AICRM_NEXT_MEDIA_STORAGE_MODE", "fake"))
    wecom = _visible_media_mode(os.getenv("AICRM_NEXT_WECOM_MEDIA_MODE", "fake"))
    if storage == wecom:
        return storage
    return f"storage:{storage},wecom:{wecom}"


def _visible_media_mode(value: str | None) -> str:
    mode = str(value or "fake").strip().lower()
    return mode if mode in {"fake", "disabled", "staging"} else "fake"


def _real_external_call_executed(payload: dict[str, Any]) -> bool:
    sync = payload.get("wecom_sync") if isinstance(payload, dict) else None
    if isinstance(sync, dict) and bool(sync.get("real_external_call_executed")):
        return True
    adapter_result = payload.get("adapter_result") if isinstance(payload, dict) else None
    if not isinstance(adapter_result, dict):
        return False
    for section in ("cloud_storage", "wecom_media"):
        value = adapter_result.get(section)
        if isinstance(value, dict) and bool(value.get("side_effect_executed")):
            return True
    safety = adapter_result.get("side_effect_safety")
    if isinstance(safety, dict):
        return bool(safety.get("side_effect_executed"))
    return False


def _with_contract(payload: dict[str, Any], *, source_status: str = "next_media_library") -> dict[str, Any]:
    result = dict(payload)
    result.setdefault("ok", True)
    if isinstance(result.get("items"), list):
        result.setdefault("count", len(result["items"]))
    result.setdefault("source_status", source_status)
    result.setdefault("route_owner", "ai_crm_next")
    result.setdefault("fallback_used", False)
    result.setdefault("real_external_call_executed", _real_external_call_executed(result))
    result.setdefault("storage_adapter_mode", _adapter_mode())
    result.setdefault("adapter_mode", _adapter_mode())
    return result


def _binary_headers() -> dict[str, str]:
    return {
        "X-AICRM-Route-Owner": "ai_crm_next",
        "X-AICRM-Fallback-Used": "false",
        "X-AICRM-Real-External-Call-Executed": "false",
        "X-AICRM-Storage-Adapter-Mode": _adapter_mode(),
    }


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ContractError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _error_response(exc: Exception, status_code: int = 400, *, headers: dict[str, str] | None = None) -> JSONResponse:
    if isinstance(exc, NotFoundError):
        status_code = 404
    elif isinstance(exc, ContractError):
        status_code = 400
    return JSONResponse(
        status_code=status_code,
        content=_with_contract({"ok": False, "error": str(exc)}, source_status="next_media_library_error"),
        headers=headers or {},
    )


@router.get("/api/admin/image-library")
def list_images(
    limit: int = 100,
    offset: int = 0,
    enabled_only: bool = True,
    q: str = "",
    category: str = "",
    tags: str = "",
    only_unlabeled: bool = False,
) -> dict:
    return _with_contract(ListMediaItemsQuery("image")(
        limit=limit,
        offset=offset,
        filters={
            "enabled_only": enabled_only,
            "q": q,
            "category": category,
            "tags": tags,
            "only_unlabeled": only_unlabeled,
        },
    ))


@router.get("/api/admin/image-library/facets")
def list_image_facets() -> dict:
    return _with_contract(ListMediaFacetsQuery("image")())


@router.post("/api/admin/image-library")
def create_image(payload: ImageUpsertRequest) -> dict:
    return _with_contract(UpsertMediaItemCommand("image")(payload), source_status="local_repository_write")


@router.post("/api/admin/image-library/from-url")
def image_from_url(payload: ImageFromUrlRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict:
    return _with_contract(ImportImageFromUrlCommand()(payload, idempotency_key=idempotency_key), source_status="fake_import_visible")


@router.post("/api/admin/image-library/from-base64")
def image_from_base64(payload: ImageFromBase64Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict:
    return _with_contract(ImportImageFromBase64Command()(payload, idempotency_key=idempotency_key), source_status="fake_import_visible")


@router.post("/api/admin/image-library/upload")
async def upload_image(
    image: UploadFile = File(...),
    name: str = Form(""),
    description: str = Form(""),
    tags: str = Form(""),
    category: str = Form(""),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    try:
        content = await image.read()
        return _with_contract(UploadImageCommand()(
            file_bytes=content,
            file_name=image.filename or "image.png",
            content_type=image.content_type or "application/octet-stream",
            name=name,
            description=description,
            tags=tags,
            category=category,
            idempotency_key=idempotency_key,
        ), source_status="local_upload")
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/image-library/{image_id}")
def get_image(image_id: str, include_data: bool = False, variant: str = "") -> dict:
    try:
        result = GetMediaItemQuery("image")(image_id, include_data=include_data)
        if variant:
            result["variant_url"] = f"/api/admin/image-library/{image_id}/variants/{variant}"
        return _with_contract(result)
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/image-library/{image_id}/thumbnail")
def get_image_thumbnail(
    image_id: str,
    size: int = Query(160),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    if size not in {160, 320, 720}:
        return JSONResponse(
            status_code=400,
            content=_with_contract(
                {"ok": False, "error": "thumbnail size must be one of 160, 320, 720"},
                source_status="next_media_library_error",
            ),
        )
    try:
        result = GetImageThumbnailQuery()(image_id, size)
        thumbnail = result["thumbnail"]
        etag = str(thumbnail.get("etag") or "")
        headers = {
            **_binary_headers(),
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": etag,
        }
        if if_none_match and etag and if_none_match == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=thumbnail.get("bytes") or b"", media_type=str(thumbnail.get("mime_type") or "image/png"), headers=headers)
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/image-library/{image_id}/variants/{variant_key}")
def get_image_variant(image_id: str, variant_key: str, if_none_match: str | None = Header(default=None, alias="If-None-Match")) -> Response:
    try:
        result = GetImageVariantQuery()(image_id, variant_key)
        variant = result["variant"]
        etag = str(variant.get("etag") or "")
        headers = {
            **_binary_headers(),
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": etag,
        }
        if if_none_match and etag and if_none_match == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=variant.get("bytes") or b"", media_type=str(variant.get("mime_type") or "image/png"), headers=headers)
    except Exception as exc:
        return _error_response(exc)


@router.put("/api/admin/image-library/{image_id}")
def update_image(image_id: str, payload: ImageUpsertRequest) -> dict:
    try:
        return _with_contract(UpsertMediaItemCommand("image")(payload, image_id), source_status="local_repository_write")
    except Exception as exc:
        return _error_response(exc)


@router.delete("/api/admin/image-library/{image_id}")
def delete_image(image_id: str, force: bool = Query(False)):
    try:
        result = _with_contract(DeleteMediaItemCommand("image")(image_id, force=force), source_status="local_delete")
        if result.get("references") and result.get("ok") is False:
            return JSONResponse(status_code=409, content=result)
        return result
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/attachment-library")
def list_attachments(limit: int = 100, offset: int = 0, enabled_only: bool = True, q: str = "") -> dict:
    return _with_contract(ListMediaItemsQuery("attachment")(limit=limit, offset=offset, filters={"enabled_only": enabled_only, "q": q}))


@router.post("/api/admin/attachment-library")
def create_attachment(payload: AttachmentUpsertRequest) -> dict:
    return _with_contract(UpsertMediaItemCommand("attachment")(payload), source_status="local_repository_write")


@router.post("/api/admin/attachment-library/upload")
async def upload_attachment(
    attachment: UploadFile = File(...),
    name: str = Form(""),
    tags: str = Form(""),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    try:
        content = await attachment.read()
        return _with_contract(UploadAttachmentCommand()(
            file_bytes=content,
            file_name=attachment.filename or "attachment.bin",
            content_type=attachment.content_type or "application/octet-stream",
            name=name,
            tags=tags,
            idempotency_key=idempotency_key,
        ), source_status="local_upload")
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/attachment-library/{attachment_id}")
def get_attachment(attachment_id: str) -> dict:
    try:
        return _with_contract(GetMediaItemQuery("attachment")(attachment_id))
    except Exception as exc:
        return _error_response(exc)


@router.put("/api/admin/attachment-library/{attachment_id}")
def update_attachment(attachment_id: str, payload: AttachmentUpsertRequest) -> dict:
    try:
        return _with_contract(UpsertMediaItemCommand("attachment")(payload, attachment_id), source_status="local_repository_write")
    except Exception as exc:
        return _error_response(exc)


@router.delete("/api/admin/attachment-library/{attachment_id}")
def delete_attachment(attachment_id: str) -> dict:
    try:
        return _with_contract(DeleteMediaItemCommand("attachment")(attachment_id), source_status="local_delete")
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/miniprogram-library")
def list_miniprograms(limit: int = 100, offset: int = 0, enabled_only: bool = True, q: str = "") -> dict:
    return _with_contract(ListMediaItemsQuery("miniprogram")(limit=limit, offset=offset, filters={"enabled_only": enabled_only, "q": q}))


@router.post("/api/admin/miniprogram-library")
def create_miniprogram(payload: MiniprogramUpsertRequest):
    started = time.perf_counter()
    def duration_headers() -> dict[str, str]:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log = logger.warning if duration_ms > 2000 else logger.info
        log("POST /api/admin/miniprogram-library duration_ms=%s", duration_ms)
        return {"X-AICRM-Media-Library-Duration-Ms": str(duration_ms)}

    try:
        result = _with_contract(UpsertMediaItemCommand("miniprogram")(payload), source_status="local_repository_write")
        return JSONResponse(content=result, headers=duration_headers())
    except Exception as exc:
        safe_log_exception(logger, "miniprogram library create failed", exc)
        return _error_response(exc, headers=duration_headers())


@router.get("/api/admin/miniprogram-library/{item_id}")
def get_miniprogram(item_id: str) -> dict:
    try:
        return _with_contract(GetMediaItemQuery("miniprogram")(item_id))
    except Exception as exc:
        return _error_response(exc)


@router.put("/api/admin/miniprogram-library/{item_id}")
def update_miniprogram(item_id: str, payload: MiniprogramUpsertRequest) -> dict:
    try:
        return _with_contract(UpsertMediaItemCommand("miniprogram")(payload, item_id), source_status="local_repository_write")
    except Exception as exc:
        return _error_response(exc)


@router.delete("/api/admin/miniprogram-library/{item_id}")
def delete_miniprogram(item_id: str) -> dict:
    try:
        return _with_contract(DeleteMediaItemCommand("miniprogram")(item_id), source_status="local_delete")
    except Exception as exc:
        return _error_response(exc)


@router.post("/api/admin/miniprogram-library/{item_id}/test-resolve")
def test_resolve_miniprogram(item_id: str) -> dict:
    try:
        return _with_contract(TestResolveMiniprogramThumbCommand()(item_id), source_status="wecom_media_plan_or_cache")
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/group-invite-library")
def list_group_invites(limit: int = 100, offset: int = 0, enabled_only: bool = True, q: str = "") -> dict:
    return _with_contract(
        ListMediaItemsQuery("group_invite")(
            limit=limit,
            offset=offset,
            filters={"enabled_only": enabled_only, "q": q},
        )
    )


@router.post("/api/admin/group-invite-library")
def create_group_invite(payload: GroupInviteUpsertRequest) -> dict:
    try:
        return _with_contract(
            UpsertMediaItemCommand("group_invite")(payload),
            source_status="local_repository_write",
        )
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/group-invite-bindings")
def list_group_invite_bindings(limit: int = 100, offset: int = 0, q: str = "") -> dict:
    return list_group_invites(limit=limit, offset=offset, enabled_only=False, q=q)


@router.post("/api/admin/group-invite-bindings/ensure")
def ensure_group_invite_binding(payload: GroupInviteBindingEnsureRequest) -> dict:
    try:
        return _with_contract(
            EnsureGroupInviteBindingCommand()(payload),
            source_status="group_invite_binding_ready",
        )
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/group-invite-bindings/{item_id}")
def get_group_invite_binding(item_id: str) -> dict:
    return get_group_invite(item_id)


@router.put("/api/admin/group-invite-bindings/{item_id}")
def update_group_invite_binding(item_id: str, payload: GroupInviteBindingUpdateRequest) -> dict:
    try:
        return _with_contract(
            UpdateGroupInviteBindingCommand()(item_id, payload),
            source_status="local_repository_write",
        )
    except Exception as exc:
        return _error_response(exc)


@router.get("/api/admin/group-invite-library/{item_id}")
def get_group_invite(item_id: str) -> dict:
    try:
        return _with_contract(GetMediaItemQuery("group_invite")(item_id))
    except Exception as exc:
        return _error_response(exc)


@router.put("/api/admin/group-invite-library/{item_id}")
def update_group_invite(item_id: str, payload: GroupInviteUpsertRequest) -> dict:
    try:
        return _with_contract(
            UpsertMediaItemCommand("group_invite")(payload, item_id),
            source_status="local_repository_write",
        )
    except Exception as exc:
        return _error_response(exc)


@router.delete("/api/admin/group-invite-library/{item_id}")
def delete_group_invite(item_id: str) -> dict:
    try:
        return _with_contract(
            DeleteMediaItemCommand("group_invite")(item_id),
            source_status="local_delete",
        )
    except Exception as exc:
        return _error_response(exc)
