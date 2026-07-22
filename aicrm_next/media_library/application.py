from __future__ import annotations

import base64
import hashlib
import os
import uuid
from typing import Any

from aicrm_next.integration_gateway.media_adapters import build_cloud_storage_adapter, build_wecom_media_adapter, extract_base64_payload
from aicrm_next.integration_gateway.wecom_group_invite_adapter import build_wecom_group_invite_adapter
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared import runtime

from .dto import AttachmentUpsertRequest, GroupInviteBindingEnsureRequest, GroupInviteBindingUpdateRequest, GroupInviteUpsertRequest, ImageFromBase64Request, ImageFromUrlRequest, ImageUpsertRequest, MiniprogramUpsertRequest, normalize_group_invite_join_url
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


def _group_invite_state(chat_id: str) -> str:
    digest = hashlib.sha256(str(chat_id or "").encode("utf-8")).hexdigest()[:20]
    return f"aicrm_gi_{digest}"


def _utf8_prefix(value: Any, byte_limit: int) -> str:
    text = str(value or "").strip()
    while len(text.encode("utf-8")) > byte_limit:
        text = text[:-1]
    return text


def _group_invite_failure(result: dict[str, Any], *, stage: str) -> ContractError:
    error_code = str(result.get("error_code") or "wecom_group_join_way_error").strip()
    if error_code in {"missing_wecom_config", "wecom_real_calls_disabled"}:
        message = "系统尚未完成企微接口配置，请管理员检查企微凭据和执行开关"
    elif result.get("retryable"):
        message = "企微接口暂时繁忙，请稍后重试"
    else:
        message = "企微未能生成该客户群的邀请链接，请确认应用具有客户群权限"
    return ContractError(f"group_invite_auto_provision_failed:{stage}:{error_code}:{message}")


class EnsureGroupInviteBindingReadyCommand:
    def __init__(self, repo: MediaLibraryRepository | None = None, adapter: Any | None = None) -> None:
        self._repo = repo or build_media_library_repository()
        self._adapter = adapter

    def _adapter_or_build(self) -> Any:
        if self._adapter is None:
            self._adapter = build_wecom_group_invite_adapter()
        return self._adapter

    def __call__(self, item_id: str, *, item: dict[str, Any] | None = None) -> dict[str, Any]:
        current = item or self._repo.get_item("group_invite", item_id)
        if not current:
            from aicrm_next.shared.errors import NotFoundError

            raise NotFoundError("group_invite item not found")
        if str(current.get("binding_status") or "") == "invalid":
            raise ContractError("group_invite_invalid:客户群已失效，请同步客户群后重试")
        if not bool(current.get("enabled", True)):
            raise ContractError("group_invite_disabled:群邀请已停用，请重新选择群聊后重试")
        current_join_url = normalize_group_invite_join_url(current.get("join_url"))
        if str(current.get("binding_status") or "") == "ready" and current_join_url:
            return {
                "ok": True,
                "item": current,
                "binding_id": current.get("id"),
                "binding_status": "ready",
                "auto_provisioned": False,
                "real_external_call_executed": False,
            }

        chat_id = str(current.get("chat_id") or ((current.get("chat_id_list") or [""])[0])).strip()
        if not chat_id:
            raise ContractError("group_invite_chat_id_missing:群邀请未绑定客户群")
        state = str(current.get("state") or "").strip() or _group_invite_state(chat_id)
        config_id = str(current.get("config_id") or "").strip()
        create_result: dict[str, Any] = {}
        real_external_call_executed = False

        if not config_id or config_id.startswith("provisioning:"):
            claim_token = f"provisioning:{uuid.uuid4().hex}"
            claim = self._repo.claim_group_invite_provisioning(str(current["id"]), claim_token, state)
            current = claim.get("item") or current
            if not claim.get("claimed"):
                if str(current.get("binding_status") or "") == "ready" and str(current.get("join_url") or "").strip():
                    return {
                        "ok": True,
                        "item": current,
                        "binding_id": current.get("id"),
                        "binding_status": "ready",
                        "auto_provisioned": False,
                        "real_external_call_executed": False,
                    }
                existing_config_id = str(current.get("config_id") or "").strip()
                if existing_config_id and not existing_config_id.startswith("provisioning:"):
                    config_id = existing_config_id
                else:
                    raise ContractError("group_invite_provisioning_in_progress:系统正在生成群邀请，请稍后重试")
            else:
                create_payload = {
                    "scene": 1,
                    "remark": _utf8_prefix(current.get("name") or current.get("title") or "AI-CRM群邀请", 30),
                    "auto_create_room": 0,
                    "chat_id_list": [chat_id],
                    "state": state,
                }
                create_result = self._adapter_or_build().create_join_way(
                    create_payload,
                    idempotency_key=f"group-invite-binding:{current['id']}:create",
                )
                real_external_call_executed = bool(create_result.get("real_external_call_executed"))
                if not create_result.get("ok"):
                    self._repo.release_group_invite_provisioning(str(current["id"]), claim_token)
                    raise _group_invite_failure(create_result, stage="create")
                config_id = str(create_result.get("config_id") or "").strip()
                if not config_id:
                    self._repo.release_group_invite_provisioning(str(current["id"]), claim_token)
                    raise ContractError("group_invite_auto_provision_failed:create:missing_config_id:企微未返回群邀请配置")
                stored = self._repo.store_group_invite_config(str(current["id"]), claim_token, config_id, state)
                if not stored:
                    raise ContractError("group_invite_auto_provision_conflict:群邀请生成结果保存冲突，请重试")
                current = stored

        get_result = self._adapter_or_build().get_join_way(
            config_id,
            idempotency_key=f"group-invite-binding:{current['id']}:get:{config_id}",
        )
        real_external_call_executed = real_external_call_executed or bool(get_result.get("real_external_call_executed"))
        if not get_result.get("ok"):
            raise _group_invite_failure(get_result, stage="get")
        join_way = get_result.get("join_way") if isinstance(get_result.get("join_way"), dict) else {}
        returned_chat_ids = [str(value or "").strip() for value in list(join_way.get("chat_id_list") or []) if str(value or "").strip()]
        if chat_id not in returned_chat_ids:
            raise ContractError("group_invite_target_mismatch:企微返回的邀请配置未绑定所选客户群")
        try:
            join_url = normalize_group_invite_join_url(join_way.get("qr_code"))
        except ValueError as exc:
            raise ContractError("group_invite_join_url_invalid:企微返回的加入群聊链接无效") from exc
        if not join_url:
            raise ContractError("group_invite_join_url_missing:企微未返回加入群聊链接")
        ready = self._repo.complete_group_invite_provisioning(str(current["id"]), config_id, state, join_url)
        return {
            "ok": True,
            "item": ready,
            "binding_id": ready.get("id"),
            "binding_status": "ready",
            "auto_provisioned": bool(create_result),
            "adapter_result": {
                "create": create_result,
                "get": {key: value for key, value in get_result.items() if key != "join_way"},
            },
            "real_external_call_executed": real_external_call_executed,
        }


class EnsureGroupInviteBindingCommand:
    def __init__(self, repo: MediaLibraryRepository | None = None, adapter: Any | None = None) -> None:
        self._repo = repo or build_media_library_repository()
        self._adapter = adapter

    def __call__(self, payload: GroupInviteBindingEnsureRequest | dict[str, Any]) -> dict[str, Any]:
        data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        item = self._repo.ensure_group_invite_binding(data)
        return EnsureGroupInviteBindingReadyCommand(self._repo, self._adapter)(str(item["id"]), item=item)


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
