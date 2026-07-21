from __future__ import annotations

import base64
import os
from typing import Any

from aicrm_next.integration_gateway.media_adapters import build_cloud_storage_adapter, build_wecom_media_adapter, extract_base64_payload
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared import runtime

from .dto import AttachmentUpsertRequest, GroupInviteBindingEnsureRequest, GroupInviteBindingUpdateRequest, GroupInviteUpsertRequest, ImageFromBase64Request, ImageFromUrlRequest, ImageUpsertRequest, MiniprogramUpsertRequest
from .repo import MediaLibraryRepository, build_media_library_repository, normalize_tags


def _side_effect_safety() -> dict[str, bool]:
    return {
        "real_cloud_upload_executed": False,
        "real_wecom_media_upload_executed": False,
        "remote_url_fetched": False,
        "side_effect_executed": False,
    }


def _content_type_from_file_name(file_name: str, fallback: str = "image/png") -> str:
    lower = file_name.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".pdf"):
        return "application/pdf"
    return fallback


def _media_adapter_summary(cloud_result: dict[str, Any] | None, wecom_result: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "cloud_storage": cloud_result or {},
        "wecom_media": wecom_result or {},
        "side_effect_safety": _side_effect_safety(),
    }


def _side_effect_plan(*, operation: str, idempotency_key: str = "", reason: str = "local_repository_write_only") -> dict[str, Any]:
    return {
        "operation": operation,
        "external_storage": "not_executed",
        "wecom_media_upload": "not_executed",
        "real_external_call": "not_executed",
        "database_write": "executed",
        "audit": "response_side_effect_plan",
        "idempotency_key": idempotency_key,
        "idempotency_required": False,
        "idempotency_reason": reason,
    }


def _upload_side_effect_plan(*, operation: str, idempotency_key: str, wecom_sync: dict[str, Any]) -> dict[str, Any]:
    plan = _side_effect_plan(
        operation=operation,
        idempotency_key=idempotency_key,
        reason="source row is durable before audited WeCom media synchronization",
    )
    status = str(wecom_sync.get("status") or "")
    if status in {"queued", "planned", "approved", "succeeded", "failed_retryable", "failed_terminal", "blocked"}:
        plan["wecom_media_upload"] = "executed" if wecom_sync.get("real_external_call_executed") else status
        plan["real_external_call"] = "executed" if wecom_sync.get("real_external_call_executed") else "not_executed"
        plan["audit"] = "external_effect_job"
    return plan


def _numeric_material_id(item: dict[str, Any]) -> int:
    try:
        return int(item.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _child_idempotency_key(idempotency_key: str | None, suffix: str) -> str | None:
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    return f"{key}:{suffix}"


def _looks_like_fake_media_id(media_id: str) -> bool:
    value = str(media_id or "").strip().lower()
    return value.startswith(("fake_", "staging_")) or value.startswith("fake://")


def _production_wecom_media_required() -> bool:
    mode = str(os.getenv("AICRM_NEXT_WECOM_MEDIA_MODE", "") or "").strip().lower()
    return runtime.production_environment() or mode == "production"


class ListMediaItemsQuery:
    def __init__(self, kind: str, repo: MediaLibraryRepository | None = None) -> None:
        self._kind = kind
        self._repo = repo or build_media_library_repository()

    def __call__(self, *, limit: int = 100, offset: int = 0, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, **self._repo.list_items(self._kind, limit=limit, offset=offset, filters=filters or {})}


class ListMediaFacetsQuery:
    def __init__(self, kind: str, repo: MediaLibraryRepository | None = None) -> None:
        self._kind = kind
        self._repo = repo or build_media_library_repository()

    def __call__(self) -> dict[str, Any]:
        return {"ok": True, **self._repo.list_facets(self._kind)}


class GetMediaItemQuery:
    def __init__(self, kind: str, repo: MediaLibraryRepository | None = None) -> None:
        self._kind = kind
        self._repo = repo or build_media_library_repository()

    def __call__(self, item_id: str, *, include_data: bool = True) -> dict[str, Any]:
        item = self._repo.get_item(self._kind, item_id, include_data=include_data)
        if not item:
            from aicrm_next.shared.errors import NotFoundError

            raise NotFoundError(f"{self._kind} item not found")
        return {"ok": True, "item": item}


class EnsureGroupInviteBindingCommand:
    def __init__(self, repo: MediaLibraryRepository | None = None) -> None:
        self._repo = repo or build_media_library_repository()

    def __call__(self, payload: GroupInviteBindingEnsureRequest | dict[str, Any]) -> dict[str, Any]:
        data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        item = self._repo.ensure_group_invite_binding(data)
        return {"ok": True, "item": item, "binding_id": item.get("id"), "binding_status": item.get("binding_status") or "pending"}


class UpdateGroupInviteBindingCommand:
    def __init__(self, repo: MediaLibraryRepository | None = None) -> None:
        self._repo = repo or build_media_library_repository()

    def __call__(self, item_id: str, payload: GroupInviteBindingUpdateRequest | dict[str, Any]) -> dict[str, Any]:
        data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        item = self._repo.save_item("group_invite", {**data, "binding_status": "ready"}, item_id)
        return {"ok": True, "item": item, "binding_id": item.get("id"), "binding_status": item.get("binding_status") or "ready"}


class GetImageVariantQuery:
    def __init__(self, repo: MediaLibraryRepository | None = None) -> None:
        self._repo = repo or build_media_library_repository()

    def __call__(self, image_id: str, variant_key: str) -> dict[str, Any]:
        variant = self._repo.get_image_variant(image_id, variant_key)
        if not variant:
            from aicrm_next.shared.errors import NotFoundError

            raise NotFoundError("image variant not found")
        return {"ok": True, "variant": variant}


class GetImageThumbnailQuery:
    def __init__(self, repo: MediaLibraryRepository | None = None) -> None:
        self._repo = repo or build_media_library_repository()

    def __call__(self, image_id: str, size: int) -> dict[str, Any]:
        thumbnail = self._repo.get_image_thumbnail(image_id, size)
        if not thumbnail:
            from aicrm_next.shared.errors import NotFoundError

            raise NotFoundError("image item not found")
        return {"ok": True, "thumbnail": thumbnail}


class UpsertMediaItemCommand:
    def __init__(self, kind: str, repo: MediaLibraryRepository | None = None) -> None:
        self._kind = kind
        self._repo = repo or build_media_library_repository()

    def __call__(
        self,
        payload: dict[str, Any] | ImageUpsertRequest | AttachmentUpsertRequest | MiniprogramUpsertRequest | GroupInviteUpsertRequest,
        item_id: str | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        data = payload.model_dump(by_alias=True, exclude_none=True) if hasattr(payload, "model_dump") else dict(payload)
        cloud_result: dict[str, Any] | None = None
        wecom_result: dict[str, Any] | None = None
        if self._kind == "image":
            file_name = str(data.get("file_name") or "image.png")
            data_url = str(data.get("data_url") or "")
            if data_url:
                data_base64 = extract_base64_payload(data_url)
                cloud_result = build_cloud_storage_adapter().put_base64_object(
                    data_base64=data_base64,
                    file_name=file_name,
                    content_type=str(data.get("content_type") or _content_type_from_file_name(file_name)),
                    idempotency_key=_child_idempotency_key(idempotency_key, "cloud"),
                )
                wecom_result = build_wecom_media_adapter().upload_image(
                    data_base64=data_base64,
                    file_name=file_name,
                    idempotency_key=_child_idempotency_key(idempotency_key, "wecom"),
                )
                data = {
                    **data,
                    "storage_key": cloud_result.get("storage_key"),
                    "public_url": cloud_result.get("public_url"),
                    "wecom_media_id": wecom_result.get("media_id"),
                    "side_effect_safety": _side_effect_safety(),
                }
        if self._kind == "attachment":
            file_name = str(data.get("file_name") or "attachment.bin")
            data_base64 = str(data.get("data_base64") or "")
            if data_base64:
                content_type = str(data.get("mime_type") or _content_type_from_file_name(file_name, "application/octet-stream"))
                cloud_result = build_cloud_storage_adapter().put_base64_object(
                    data_base64=data_base64,
                    file_name=file_name,
                    content_type=content_type,
                    idempotency_key=_child_idempotency_key(idempotency_key, "cloud"),
                )
                wecom_result = build_wecom_media_adapter().upload_attachment(
                    data_base64=data_base64,
                    file_name=file_name,
                    content_type=content_type,
                    idempotency_key=_child_idempotency_key(idempotency_key, "wecom"),
                )
                data = {
                    **data,
                    "storage_key": cloud_result.get("storage_key"),
                    "public_url": cloud_result.get("public_url"),
                    "wecom_media_id": wecom_result.get("media_id"),
                    "side_effect_safety": _side_effect_safety(),
                }
        item = self._repo.save_item(self._kind, data, item_id)
        result = {"ok": True, "item": item}
        if self._kind == "miniprogram" and data.get("resolve_thumb_media", True) and item.get("thumb_image_id"):
            thumb_resolve = TestResolveMiniprogramThumbCommand(self._repo)(str(item["id"]))
            result["thumb_resolve"] = thumb_resolve
            if thumb_resolve.get("ok") and isinstance(thumb_resolve.get("item"), dict):
                result["item"] = thumb_resolve["item"]
        if cloud_result or wecom_result:
            result["adapter_result"] = _media_adapter_summary(cloud_result, wecom_result)
            result["side_effect_plan"] = _side_effect_plan(
                operation=f"{self._kind}_upsert_adapter_plan",
                idempotency_key=str(idempotency_key or ""),
                reason="guarded_adapter_idempotency_key_used" if idempotency_key else "guarded_adapter_deterministic_key",
            )
        else:
            result["side_effect_plan"] = _side_effect_plan(operation=f"{self._kind}_upsert")
        return result


class DeleteMediaItemCommand:
    def __init__(self, kind: str, repo: MediaLibraryRepository | None = None) -> None:
        self._kind = kind
        self._repo = repo or build_media_library_repository()

    def __call__(self, item_id: str, *, force: bool = False) -> dict[str, Any]:
        result = self._repo.delete_item(self._kind, item_id, force=force)
        return {
            **result,
            "side_effect_plan": _side_effect_plan(
                operation=f"{self._kind}_delete",
                reason="delete is a local repository mutation; external storage and WeCom media references are not deleted by this route",
            ),
        }


def _validate_image_upload(*, file_bytes: bytes, file_name: str, content_type: str) -> str:
    if not file_bytes:
        raise ContractError("invalid_image: image file is empty")
    if len(file_bytes) > 10 * 1024 * 1024:
        raise ContractError("request_body_too_large: image file too large; max 10MB")
    lower_name = file_name.lower()
    normalized = "image/jpeg" if content_type in {"image/jpg", "image/jpeg"} or lower_name.endswith((".jpg", ".jpeg")) else content_type
    if lower_name.endswith(".webp") and normalized in {"application/octet-stream", "image/webp"}:
        normalized = "image/webp"
    if normalized == "application/octet-stream":
        if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            normalized = "image/png"
        elif file_bytes.startswith(b"\xff\xd8"):
            normalized = "image/jpeg"
        elif file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP":
            normalized = "image/webp"
    if normalized not in {"image/png", "image/jpeg", "image/webp"}:
        raise ContractError("unsupported_mime_type: only JPG/PNG/WEBP images are supported")
    if normalized == "image/png" and not file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ContractError("invalid_image: invalid PNG image")
    if normalized == "image/jpeg" and not file_bytes.startswith(b"\xff\xd8"):
        raise ContractError("invalid_image: invalid JPG image")
    if normalized == "image/webp" and not (file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP"):
        raise ContractError("invalid_image: invalid WEBP image")
    return normalized


class UploadImageCommand:
    def __init__(self, repo: MediaLibraryRepository | None = None) -> None:
        self._repo = repo or build_media_library_repository()

    def __call__(
        self,
        *,
        file_bytes: bytes,
        file_name: str,
        content_type: str,
        name: str = "",
        description: str = "",
        tags: Any = None,
        category: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        mime_type = _validate_image_upload(file_bytes=file_bytes, file_name=file_name, content_type=content_type)
        item = self._repo.save_item(
            "image",
            {
                "name": name or file_name,
                "file_name": file_name,
                "source": "upload",
                "source_url": "",
                "data_base64": base64.b64encode(file_bytes).decode("ascii"),
                "mime_type": mime_type,
                "content_type": mime_type,
                "file_size": len(file_bytes),
                "description": description,
                "tags": normalize_tags(tags),
                "category": category,
                "enabled": True,
                "ai_metadata": {},
            },
        )
        from aicrm_next.wecom_media_jobs import sync_uploaded_material

        material_id = _numeric_material_id(item)
        wecom_sync = (
            sync_uploaded_material(
                material_kind="image",
                material_id=material_id,
                upload_kind="image",
                actor="media_library_upload",
                idempotency_key=str(idempotency_key or ""),
            )
            if material_id > 0
            else {"status": "skipped", "reason": "non_persistent_material_id", "real_external_call_executed": False}
        )
        if wecom_sync.get("status") == "succeeded":
            item = self._repo.get_item("image", str(item.get("id") or 0)) or item
        return {
            "ok": True,
            "item": item,
            "source_status": "local_upload_wecom_synced" if wecom_sync.get("status") == "succeeded" else "local_upload",
            "wecom_sync": wecom_sync,
            "real_external_call_executed": bool(wecom_sync.get("real_external_call_executed")),
            "side_effect_plan": _upload_side_effect_plan(
                operation="image_upload",
                idempotency_key=str(idempotency_key or ""),
                wecom_sync=wecom_sync,
            ),
        }


class UploadAttachmentCommand:
    def __init__(self, repo: MediaLibraryRepository | None = None) -> None:
        self._repo = repo or build_media_library_repository()

    def __call__(self, *, file_bytes: bytes, file_name: str, content_type: str, name: str = "", tags: Any = None, idempotency_key: str | None = None) -> dict[str, Any]:
        if not file_bytes:
            raise ContractError("invalid_attachment: attachment file is empty")
        normalized_type = str(content_type or "application/octet-stream").split(";")[0].strip().lower()
        if file_name.lower().endswith(".pdf") and normalized_type in {"application/octet-stream", "application/pdf"}:
            normalized_type = "application/pdf"
        if normalized_type == "application/pdf":
            if len(file_bytes) > 50 * 1024 * 1024:
                raise ContractError("request_body_too_large: pdf file too large; max 50MB")
            if not file_bytes.startswith(b"%PDF-"):
                raise ContractError("invalid_pdf: invalid PDF file")
        item = self._repo.save_item(
            "attachment",
            {
                "name": name or file_name,
                "file_name": file_name,
                "mime_type": normalized_type or "application/octet-stream",
                "file_size": len(file_bytes),
                "data_base64": base64.b64encode(file_bytes).decode("ascii"),
                "tags": normalize_tags(tags),
                "enabled": True,
            },
        )
        from aicrm_next.wecom_media_jobs import sync_uploaded_material

        material_id = _numeric_material_id(item)
        wecom_sync = (
            sync_uploaded_material(
                material_kind="attachment",
                material_id=material_id,
                upload_kind="attachment",
                actor="media_library_upload",
                idempotency_key=str(idempotency_key or ""),
            )
            if material_id > 0
            else {"status": "skipped", "reason": "non_persistent_material_id", "real_external_call_executed": False}
        )
        if wecom_sync.get("status") == "succeeded":
            item = self._repo.get_item("attachment", str(item.get("id") or 0)) or item
        return {
            "ok": True,
            "item": item,
            "source_status": "local_upload_wecom_synced" if wecom_sync.get("status") == "succeeded" else "local_upload",
            "wecom_sync": wecom_sync,
            "real_external_call_executed": bool(wecom_sync.get("real_external_call_executed")),
            "side_effect_plan": _upload_side_effect_plan(
                operation="attachment_upload",
                idempotency_key=str(idempotency_key or ""),
                wecom_sync=wecom_sync,
            ),
        }


class TestResolveMiniprogramThumbCommand:
    def __init__(self, repo: MediaLibraryRepository | None = None) -> None:
        self._repo = repo or build_media_library_repository()

    def __call__(self, item_id: str) -> dict[str, Any]:
        item = self._repo.get_item("miniprogram", item_id)
        if not item:
            from aicrm_next.shared.errors import NotFoundError

            raise NotFoundError("miniprogram item not found")
        thumb_media_id = str(item.get("thumb_media_id") or "")
        if thumb_media_id and not _looks_like_fake_media_id(thumb_media_id):
            return {"ok": True, "thumb_media_id": thumb_media_id, "item": item, "source": "miniprogram_cache"}
        thumb_image_id = item.get("thumb_image_id")
        if not thumb_image_id:
            return {"ok": False, "error": "thumb_image_id is required before resolving WeCom media"}
        image = self._repo.get_item("image", str(thumb_image_id), include_data=True)
        if not image:
            return {"ok": False, "error": "thumb image item is unavailable"}
        image_media_id = str(image.get("thumb_media_id") or image.get("wecom_media_id") or "")
        if image_media_id and not _looks_like_fake_media_id(image_media_id):
            updated = self._repo.save_item("miniprogram", {"thumb_media_id": image_media_id}, item_id)
            return {"ok": True, "thumb_media_id": image_media_id, "item": updated, "source": "image_library_cache"}

        if _production_wecom_media_required():
            return {
                "ok": False,
                "error": "real_wecom_media_resolve_failed",
                "error_message": "image_library must contain a real WeCom media_id before miniprogram material can be resolved in production",
                "thumb_image_id": thumb_image_id,
            }

        if not image.get("data_base64"):
            return {"ok": False, "error": "thumb image data is unavailable"}
        result = build_wecom_media_adapter().upload_image(
            data_base64=str(image.get("data_base64") or ""),
            file_name=str(image.get("file_name") or "thumb.png"),
        )
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error_message") or result.get("error_code") or "wecom media adapter unavailable"}
        thumb_media_id = str(result.get("media_id") or "")
        updated = self._repo.save_item("miniprogram", {"thumb_media_id": thumb_media_id}, item_id)
        return {"ok": True, "thumb_media_id": thumb_media_id, "item": updated, "adapter_result": result}


class ImportImageFromUrlCommand:
    def __init__(self, repo: MediaLibraryRepository | None = None) -> None:
        self._repo = repo or build_media_library_repository()

    def __call__(self, payload: ImageFromUrlRequest, *, idempotency_key: str | None = None) -> dict[str, Any]:
        name = payload.name or "外链图片样例"
        cloud_result = build_cloud_storage_adapter().put_remote_reference(
            source_url=payload.url,
            file_name="from-url.png",
            content_type="image/png",
            idempotency_key=_child_idempotency_key(idempotency_key, "cloud"),
        )
        wecom_result = build_wecom_media_adapter().resolve_media_id(
            reference_url=str(cloud_result.get("reference_url") or payload.url),
            file_name="from-url.png",
            idempotency_key=_child_idempotency_key(idempotency_key, "wecom"),
        )
        item = self._repo.save_item(
            "image",
            {
                "name": name,
                "file_name": "from-url.png",
                "content_type": "image/png",
                "file_size": 16,
                "width": 1,
                "height": 1,
                "data_url": "data:image/png;base64,ZmFrZQ==",
                "source_url": payload.url,
                "tags": payload.tags,
                "source_status": "fake_import",
                "storage_key": cloud_result.get("storage_key"),
                "public_url": cloud_result.get("public_url"),
                "wecom_media_id": wecom_result.get("media_id"),
                "side_effect_safety": _side_effect_safety(),
            },
        )
        return {
            "ok": True,
            "item": item,
            "source_status": "fake_import",
            "adapter_result": _media_adapter_summary(cloud_result, wecom_result),
            "side_effect_plan": _side_effect_plan(
                operation="image_from_url",
                idempotency_key=str(idempotency_key or ""),
                reason="guarded adapters return fake/staging references in tests; production real calls remain blocked unless explicitly enabled",
            ),
        }


class ImportImageFromBase64Command:
    def __init__(self, repo: MediaLibraryRepository | None = None) -> None:
        self._repo = repo or build_media_library_repository()

    def __call__(self, payload: ImageFromBase64Request, *, idempotency_key: str | None = None) -> dict[str, Any]:
        content_type = _content_type_from_file_name(payload.file_name, "image/png")
        data_base64 = extract_base64_payload(payload.data_base64)
        cloud_result = build_cloud_storage_adapter().put_base64_object(
            data_base64=data_base64,
            file_name=payload.file_name,
            content_type=content_type,
            idempotency_key=_child_idempotency_key(idempotency_key, "cloud"),
        )
        wecom_result = build_wecom_media_adapter().upload_image(
            data_base64=data_base64,
            file_name=payload.file_name,
            idempotency_key=_child_idempotency_key(idempotency_key, "wecom"),
        )
        item = self._repo.save_item(
            "image",
            {
                "name": payload.name or "Base64 图片样例",
                "file_name": payload.file_name,
                "content_type": content_type,
                "file_size": len(data_base64),
                "width": 1,
                "height": 1,
                "data_url": "data:image/png;base64," + data_base64,
                "tags": payload.tags,
                "source_status": "fake_import",
                "storage_key": cloud_result.get("storage_key"),
                "public_url": cloud_result.get("public_url"),
                "wecom_media_id": wecom_result.get("media_id"),
                "side_effect_safety": _side_effect_safety(),
            },
        )
        return {
            "ok": True,
            "item": item,
            "source_status": "fake_import",
            "adapter_result": _media_adapter_summary(cloud_result, wecom_result),
            "side_effect_plan": _side_effect_plan(
                operation="image_from_base64",
                idempotency_key=str(idempotency_key or ""),
                reason="guarded adapters use the Idempotency-Key when provided; production real calls remain blocked unless explicitly enabled",
            ),
        }
