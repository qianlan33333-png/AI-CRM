from __future__ import annotations

import base64
import json
from typing import Any

from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.repository_provider import blocked_production_payload

from .dto import MaterialPickerListRequest, SendContentPackage, SendContentPreviewRequest
from .repo import SendContentRepository, build_send_content_repository

_MATERIAL_ASSET_TYPES = ("image", "miniprogram", "attachment", "group_invite")
_MATERIAL_ASSET_CURSOR_VERSION = 1
_MATERIAL_FIELD_BY_TYPE = {
    "image": "image_library_ids",
    "miniprogram": "miniprogram_library_ids",
    "attachment": "attachment_library_ids",
    "group_invite": "group_invite_library_ids",
}
_MATERIAL_CHANNEL_COMPATIBILITY = {
    "send_content": set(_MATERIAL_ASSET_TYPES),
    "wecom_private": set(_MATERIAL_ASSET_TYPES),
    "channel_welcome": set(_MATERIAL_ASSET_TYPES),
    "group_ops": set(_MATERIAL_ASSET_TYPES),
    "cloud_plan": set(_MATERIAL_ASSET_TYPES),
    "radar_link": {"image", "attachment"},
    "wechat_pay_product_page": {"image"},
}


def normalize_send_content_package(
    content_package: SendContentPackage | dict[str, Any] | None,
    *,
    text_enabled: bool = True,
    require_body: bool = True,
) -> dict[str, Any]:
    if content_package is None:
        content_package = SendContentPackage()
    if not isinstance(content_package, SendContentPackage):
        content_package = SendContentPackage.model_validate(content_package)
    content_text = str(content_package.content_text or "").strip()
    if not text_enabled:
        content_text = ""
    if len(content_text) > 4000:
        raise ContractError("文本内容不能超过 4000 字")
    image_ids = _normalize_ids(content_package.image_library_ids, field_name="image_library_ids", max_count=3)
    miniprogram_ids = _normalize_ids(content_package.miniprogram_library_ids, field_name="miniprogram_library_ids", max_count=1)
    attachment_ids = _normalize_ids(content_package.attachment_library_ids, field_name="attachment_library_ids", max_count=9)
    group_invite_ids = _normalize_ids(content_package.group_invite_library_ids, field_name="group_invite_library_ids", max_count=1)
    normalized = {
        "content_text": content_text,
        "image_library_ids": image_ids,
        "miniprogram_library_ids": miniprogram_ids,
        "attachment_library_ids": attachment_ids,
        "group_invite_library_ids": group_invite_ids,
    }
    if require_body and not any([content_text, image_ids, miniprogram_ids, attachment_ids, group_invite_ids]):
        raise ContractError("内容包不能为空，请填写文本或选择素材")
    return normalized


class NormalizeSendContentPackageCommand:
    def execute(
        self,
        content_package: SendContentPackage | dict[str, Any],
        *,
        text_enabled: bool = True,
        require_body: bool = True,
    ) -> dict[str, Any]:
        return normalize_send_content_package(
            content_package,
            text_enabled=text_enabled,
            require_body=require_body,
        )

    __call__ = execute


class PreviewSendContentPackageQuery:
    def __init__(self, repo: SendContentRepository | None = None) -> None:
        self._repo = repo

    def execute(self, request: SendContentPreviewRequest) -> dict[str, Any]:
        content_package = normalize_send_content_package(
            request.content_package,
            text_enabled=request.text_enabled,
            require_body=request.require_body,
        )
        repo = self._repo_or_build()
        materials = _preview_materials(repo, content_package)
        return {
            "ok": True,
            "content_package": content_package,
            "preview": {
                "content_text": content_package["content_text"],
                "material_summary": {
                    "image_count": len(content_package["image_library_ids"]),
                    "miniprogram_count": len(content_package["miniprogram_library_ids"]),
                    "attachment_count": len(content_package["attachment_library_ids"]),
                    "group_invite_count": len(content_package["group_invite_library_ids"]),
                },
                "materials": materials,
            },
        }

    def _repo_or_build(self) -> SendContentRepository:
        if self._repo is None:
            self._repo = build_send_content_repository()
        return self._repo

    __call__ = execute


class ListMaterialPickerItemsQuery:
    def __init__(self, repo: SendContentRepository | None = None) -> None:
        self._repo = repo

    def execute(self, request: MaterialPickerListRequest) -> dict[str, Any]:
        repo = self._repo_or_build()
        limit = max(1, min(int(request.limit or 50), 100))
        offset = max(0, int(request.offset or 0))
        result = repo.list_materials(
            request.type,
            q=request.q,
            enabled_only=request.enabled_only,
            limit=limit,
            offset=offset,
        )
        items = [_picker_item_with_flat_metadata(item) for item in result.get("items") or []]
        if request.type == "attachment":
            items = [item for item in items if str(item.get("mime_type") or "").split(";")[0].strip().lower() == "application/pdf"]
        return {
            "ok": True,
            "type": request.type,
            "items": items,
            "total": len(items) if request.type == "attachment" else int(result.get("total") or 0),
            "limit": limit,
            "offset": offset,
        }

    def _repo_or_build(self) -> SendContentRepository:
        if self._repo is None:
            self._repo = build_send_content_repository()
        return self._repo

    __call__ = execute


class ListMaterialAssetsQuery:
    def __init__(self, repo: SendContentRepository | None = None) -> None:
        self._repo = repo

    def execute(
        self,
        *,
        asset_type: str = "all",
        q: str = "",
        enabled_only: bool = True,
        limit: int = 50,
        offset: int = 0,
        cursor: str = "",
    ) -> dict[str, Any]:
        normalized_type = str(asset_type or "all").strip().lower()
        if normalized_type not in {"all", *_MATERIAL_ASSET_TYPES}:
            raise ContractError("素材类型必须是 all、image、miniprogram、attachment 或 group_invite")

        limit = max(1, min(int(limit or 50), 100))
        offset = max(0, int(offset or 0))
        cursor_state = _decode_material_assets_cursor(cursor)
        if cursor_state and cursor_state.get("type") != normalized_type:
            raise ContractError("素材资产游标与当前素材类型不匹配")
        if cursor_state:
            offset = max(0, int(cursor_state.get("offset") or 0))
        repo = self._repo_or_build()
        is_all = normalized_type == "all"
        material_types = list(_MATERIAL_ASSET_TYPES) if normalized_type == "all" else [normalized_type]
        if is_all:
            return self._execute_all_types(
                repo=repo,
                material_types=material_types,
                q=q,
                enabled_only=enabled_only,
                limit=limit,
                offset=offset,
                normalized_type=normalized_type,
                cursor_state=cursor_state,
            )

        material_type = material_types[0]
        source_offset = offset if not cursor_state else max(0, int(cursor_state.get("source_offset") or 0))
        result = repo.list_materials(
            material_type,
            q=q,
            enabled_only=enabled_only,
            limit=limit,
            offset=source_offset,
        )
        total = int(result.get("total") or 0)
        assets = [_material_asset_item(material_type, item) for item in result.get("items") or []]
        next_source_offset = source_offset + len(assets)
        has_more = next_source_offset < total
        next_offset = offset + len(assets)
        source_cursor = _source_cursor(material_type=material_type, source_index=0, offset=next_source_offset)

        return {
            "ok": True,
            "read_model": "material_assets",
            "type": normalized_type,
            "assets": assets[:limit],
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_cursor": _encode_material_assets_cursor(
                {
                    "type": normalized_type,
                    "offset": next_offset,
                    "source_index": 0,
                    "source_offset": next_source_offset,
                }
            )
            if has_more
            else "",
            "has_more": has_more,
            "sort_key": "asset_type_order:source_offset",
            "source_cursor": source_cursor if has_more else _source_cursor(material_type=material_type, source_index=0, offset=total),
        }

    def _execute_all_types(
        self,
        *,
        repo: SendContentRepository,
        material_types: list[str],
        q: str,
        enabled_only: bool,
        limit: int,
        offset: int,
        normalized_type: str,
        cursor_state: dict[str, Any],
    ) -> dict[str, Any]:
        remaining_limit = limit
        assets: list[dict[str, Any]] = []
        total = 0
        source_totals: list[int] = []

        for material_type in material_types:
            probe = repo.list_materials(
                material_type,
                q=q,
                enabled_only=enabled_only,
                limit=1,
                offset=0,
            )
            source_total = int(probe.get("total") or 0)
            source_totals.append(source_total)
            total += source_total

        if cursor_state:
            start_source_index = max(0, min(int(cursor_state.get("source_index") or 0), len(material_types)))
            start_source_offset = max(0, int(cursor_state.get("source_offset") or 0))
        else:
            start_source_index, start_source_offset = _source_position_from_global_offset(source_totals, offset)

        next_source_index = start_source_index
        next_source_offset = start_source_offset
        has_more = False

        for source_index in range(start_source_index, len(material_types)):
            material_type = material_types[source_index]
            source_total = source_totals[source_index]
            source_offset = start_source_offset if source_index == start_source_index else 0
            if remaining_limit <= 0:
                has_more = _has_more_from_source_position(source_totals, source_index, source_offset)
                break
            if source_offset >= source_total:
                next_source_index = source_index + 1
                next_source_offset = 0
                continue
            page = repo.list_materials(
                material_type,
                q=q,
                enabled_only=enabled_only,
                limit=remaining_limit,
                offset=source_offset,
            )
            page_items = list(page.get("items") or [])
            assets.extend(_material_asset_item(material_type, item) for item in page_items)
            remaining_limit -= len(page_items)
            next_source_index = source_index
            next_source_offset = source_offset + len(page_items)
            if next_source_offset < source_total:
                has_more = True
                break
            next_source_index = source_index + 1
            next_source_offset = 0

        if not has_more:
            next_source_index, next_source_offset, has_more = _next_nonempty_source_position(
                source_totals,
                next_source_index,
                next_source_offset,
            )

        next_offset = offset + len(assets)
        source_cursor = _source_cursor(
            material_type=material_types[next_source_index] if next_source_index < len(material_types) else "",
            source_index=next_source_index,
            offset=next_source_offset,
        )

        return {
            "ok": True,
            "read_model": "material_assets",
            "type": normalized_type,
            "assets": assets,
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_cursor": _encode_material_assets_cursor(
                {
                    "type": normalized_type,
                    "offset": next_offset,
                    "source_index": next_source_index,
                    "source_offset": next_source_offset,
                }
            )
            if has_more
            else "",
            "has_more": has_more,
            "sort_key": "asset_type_order:source_offset",
            "source_cursor": source_cursor,
        }

    def _repo_or_build(self) -> SendContentRepository:
        if self._repo is None:
            self._repo = build_send_content_repository()
        return self._repo

    __call__ = execute


class GetMaterialAssetUsageQuery:
    def __init__(self, repo: SendContentRepository | None = None) -> None:
        self._repo = repo

    def execute(self, material_asset_id: str, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        material_type, source_id = _parse_material_asset_id(material_asset_id)
        limit = max(1, min(int(limit or 100), 100))
        offset = max(0, int(offset or 0))
        repo = self._repo_or_build()
        result = repo.list_material_asset_usage(material_type, source_id, limit=limit, offset=offset)
        items = list(result.get("items") or [])
        return {
            "ok": True,
            "read_model": "material_asset_usage",
            "material_asset_id": f"{material_type}:{source_id}",
            "asset_type": material_type,
            "source_id": source_id,
            "usage": items,
            "items": items,
            "total": int(result.get("total") or len(items)),
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < int(result.get("total") or len(items)),
        }

    def _repo_or_build(self) -> SendContentRepository:
        if self._repo is None:
            self._repo = build_send_content_repository()
        return self._repo

    __call__ = execute


class ValidateMaterialAssetsQuery:
    def __init__(self, repo: SendContentRepository | None = None) -> None:
        self._repo = repo

    def execute(
        self,
        content_package: SendContentPackage | dict[str, Any] | None,
        *,
        channel: str = "send_content",
        text_enabled: bool = True,
        require_body: bool = False,
    ) -> dict[str, Any]:
        normalized_channel = str(channel or "send_content").strip().lower()
        if normalized_channel not in _MATERIAL_CHANNEL_COMPATIBILITY:
            raise ContractError("素材校验 channel 不支持")
        normalized_package = normalize_send_content_package(
            content_package,
            text_enabled=text_enabled,
            require_body=require_body,
        )
        repo = self._repo_or_build()
        issues: list[dict[str, Any]] = []
        materials: list[dict[str, Any]] = []
        for material_type, field_name in _MATERIAL_FIELD_BY_TYPE.items():
            requested_ids = list(normalized_package.get(field_name) or [])
            if not requested_ids:
                continue
            rows = repo.get_materials_by_ids(material_type, requested_ids)
            by_id = {int(row.get("library_id") or 0): row for row in rows}
            for source_id in requested_ids:
                row = by_id.get(int(source_id))
                asset_id = f"{material_type}:{int(source_id)}"
                if row is None:
                    issues.append(_material_validation_issue(asset_id, "material_missing", "素材不存在", field_name))
                    continue
                material = _material_asset_item(material_type, row)
                materials.append(material)
                issues.extend(_validate_material_asset(material, channel=normalized_channel, field_name=field_name))
        blocking_issues = [issue for issue in issues if issue.get("severity") == "error"]
        return {
            "ok": True,
            "read_model": "material_asset_validation",
            "valid": not blocking_issues,
            "channel": normalized_channel,
            "content_package": normalized_package,
            "materials": materials,
            "issues": issues,
            "issue_count": len(issues),
        }

    def _repo_or_build(self) -> SendContentRepository:
        if self._repo is None:
            self._repo = build_send_content_repository()
        return self._repo

    __call__ = execute


def assert_group_invite_bindings_ready(
    content_package: SendContentPackage | dict[str, Any] | None,
    *,
    repo: SendContentRepository | None = None,
    channel: str = "send_content",
) -> None:
    package = content_package.model_dump() if isinstance(content_package, SendContentPackage) else dict(content_package or {})
    group_invite_ids = list(package.get("group_invite_library_ids") or [])
    if not group_invite_ids:
        return
    result = ValidateMaterialAssetsQuery(repo)(
        {
            "content_text": "",
            "image_library_ids": [],
            "miniprogram_library_ids": [],
            "attachment_library_ids": [],
            "group_invite_library_ids": group_invite_ids,
        },
        channel=channel,
    )
    blocking = [issue for issue in result.get("issues") or [] if issue.get("severity") == "error"]
    if blocking:
        ids = ",".join(str(value) for value in group_invite_ids)
        raise ContractError(f"group_invite_not_ready:ids={ids}:系统未能自动生成群邀请，请重新选择群聊后重试")


def _validate_material_asset(material: dict[str, Any], *, channel: str, field_name: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    asset_id = str(material.get("material_asset_id") or "")
    material_type = str(material.get("asset_type") or "")
    if material_type not in _MATERIAL_CHANNEL_COMPATIBILITY[channel]:
        issues.append(_material_validation_issue(asset_id, "material_channel_incompatible", "素材类型与发送渠道不兼容", field_name))
    if not bool(material.get("enabled", True)):
        issues.append(_material_validation_issue(asset_id, "material_disabled", "素材已停用", field_name))
    metadata = material.get("metadata") if isinstance(material.get("metadata"), dict) else {}
    if _contains_payload_leak(material):
        issues.append(_material_validation_issue(asset_id, "material_payload_leak", "素材响应包含 base64 或 data_url 原始载荷", field_name))
    if material_type == "group_invite":
        binding_status = str(metadata.get("binding_status") or ("ready" if metadata.get("join_url") else "pending")).strip()
        if binding_status != "ready" or not str(metadata.get("join_url") or "").strip():
            issues.append(_material_validation_issue(asset_id, "group_invite_not_ready", "系统未能自动生成群邀请，请重新选择群聊后重试", field_name))
            return issues
    missing = _missing_metadata_fields(material_type, material, metadata)
    for field in missing:
        issues.append(_material_validation_issue(asset_id, "material_metadata_incomplete", f"素材元数据缺失：{field}", field_name))
    return issues


def _missing_metadata_fields(material_type: str, material: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    if material_type == "image":
        missing = []
        if not str(metadata.get("mime_type") or "").strip():
            missing.append("mime_type")
        if not (str(material.get("thumbnail_url") or "").strip() or str(metadata.get("file_name") or "").strip()):
            missing.append("thumbnail_url")
        return missing
    if material_type == "miniprogram":
        missing = [field for field in ("appid", "pagepath") if not str(metadata.get(field) or "").strip()]
        if not (
            str(metadata.get("thumb_media_id") or "").strip()
            or str(metadata.get("thumb_image_id") or "").strip()
            or str(material.get("thumbnail_url") or "").strip()
        ):
            missing.append("thumb")
        return missing
    if material_type == "attachment":
        missing = [field for field in ("file_name", "mime_type") if not str(metadata.get(field) or "").strip()]
        if int(metadata.get("file_size") or 0) <= 0:
            missing.append("file_size")
        return missing
    if material_type == "group_invite":
        binding_status = str(metadata.get("binding_status") or ("ready" if metadata.get("join_url") else "pending")).strip()
        if binding_status != "ready":
            return ["binding_status"]
        missing = []
        if not str(material.get("title") or "").strip():
            missing.append("title")
        if not str(metadata.get("join_url") or "").strip():
            missing.append("join_url")
        return missing
    return ["asset_type"]


def _contains_payload_leak(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"data_base64", "data_url", "base64"}:
                return True
            if _contains_payload_leak(item):
                return True
    if isinstance(value, list):
        return any(_contains_payload_leak(item) for item in value)
    return False


def _material_validation_issue(material_asset_id: str, code: str, message: str, field_path: str) -> dict[str, Any]:
    return {
        "severity": "error",
        "code": code,
        "message": message,
        "material_asset_id": material_asset_id,
        "field_path": field_path,
    }


def _parse_material_asset_id(material_asset_id: str) -> tuple[str, int]:
    raw = str(material_asset_id or "").strip()
    material_type, separator, source_id_text = raw.partition(":")
    material_type = material_type.strip().lower()
    if not separator or material_type not in _MATERIAL_ASSET_TYPES:
        raise ContractError("material_asset_id 必须形如 image:12、miniprogram:34、attachment:56 或 group_invite:78")
    try:
        source_id = int(source_id_text)
    except ValueError as exc:
        raise ContractError("material_asset_id 的 source_id 必须是正整数") from exc
    if source_id <= 0:
        raise ContractError("material_asset_id 的 source_id 必须是正整数")
    return material_type, source_id


def _encode_material_assets_cursor(payload: dict[str, Any]) -> str:
    body = {
        "v": _MATERIAL_ASSET_CURSOR_VERSION,
        "type": str(payload.get("type") or "all"),
        "offset": max(0, int(payload.get("offset") or 0)),
        "source_index": max(0, int(payload.get("source_index") or 0)),
        "source_offset": max(0, int(payload.get("source_offset") or 0)),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def _decode_material_assets_cursor(cursor: str | None) -> dict[str, Any]:
    token = str(cursor or "").strip()
    if not token:
        return {}
    try:
        padding = "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(f"{token}{padding}".encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
        version = _cursor_int(payload.get("v"))
        offset = _cursor_int(payload.get("offset"))
        source_index = _cursor_int(payload.get("source_index"))
        source_offset = _cursor_int(payload.get("source_offset"))
    except Exception as exc:
        raise ContractError("素材资产游标无效") from exc
    if not isinstance(payload, dict) or version != _MATERIAL_ASSET_CURSOR_VERSION:
        raise ContractError("素材资产游标无效")
    cursor_type = str(payload.get("type") or "").strip().lower()
    if cursor_type not in {"all", *_MATERIAL_ASSET_TYPES}:
        raise ContractError("素材资产游标无效")
    return {
        "type": cursor_type,
        "offset": max(0, offset),
        "source_index": max(0, source_index),
        "source_offset": max(0, source_offset),
    }


def _cursor_int(value: Any) -> int:
    return int(value or 0)


def _source_position_from_global_offset(source_totals: list[int], offset: int) -> tuple[int, int]:
    remaining = max(0, int(offset or 0))
    for source_index, source_total in enumerate(source_totals):
        if remaining < source_total:
            return source_index, remaining
        remaining -= source_total
    return len(source_totals), 0


def _has_more_from_source_position(source_totals: list[int], source_index: int, source_offset: int) -> bool:
    if source_index < len(source_totals) and source_offset < source_totals[source_index]:
        return True
    return any(source_total > 0 for source_total in source_totals[source_index + 1 :])


def _next_nonempty_source_position(source_totals: list[int], source_index: int, source_offset: int) -> tuple[int, int, bool]:
    if source_index < len(source_totals) and source_offset < source_totals[source_index]:
        return source_index, source_offset, True
    for next_index in range(max(0, source_index), len(source_totals)):
        if source_totals[next_index] > 0:
            return next_index, 0, True
    return len(source_totals), 0, False


def _source_cursor(*, material_type: str, source_index: int, offset: int) -> dict[str, Any]:
    return {
        "material_type": material_type,
        "source_index": max(0, int(source_index or 0)),
        "offset": max(0, int(offset or 0)),
    }


def _picker_item_with_flat_metadata(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
    for key in ("file_name", "mime_type", "file_size"):
        if key not in normalized and key in metadata:
            normalized[key] = metadata[key]
    return normalized


def _material_asset_item(material_type: str, item: dict[str, Any]) -> dict[str, Any]:
    picker_item = _picker_item_with_flat_metadata(item)
    library_id = int(picker_item.get("library_id") or 0)
    source_table = {
        "image": "image_library",
        "miniprogram": "miniprogram_library",
        "attachment": "attachment_library",
        "group_invite": "group_invite_library",
    }[material_type]
    return {
        "material_asset_id": f"{material_type}:{library_id}",
        "asset_type": material_type,
        "source_table": source_table,
        "source_id": library_id,
        "title": str(picker_item.get("title") or ""),
        "subtitle": str(picker_item.get("subtitle") or ""),
        "thumbnail_url": str(picker_item.get("thumbnail_url") or ""),
        "enabled": bool(picker_item.get("enabled", True)),
        "metadata": picker_item.get("metadata") if isinstance(picker_item.get("metadata"), dict) else {},
        "picker_item": picker_item,
    }


def send_content_production_unavailable_payload(detail: str | None = None) -> dict[str, Any]:
    payload = blocked_production_payload(
        capability_owner="aicrm_next.send_content",
        detail=detail or "send content production repository is unavailable.",
    )
    payload.update({"status_code": 503, "error_code": "production_unavailable", "route_owner": "ai_crm_next"})
    return payload


def _normalize_ids(values: list[Any], *, field_name: str, max_count: int) -> list[int]:
    normalized: list[int] = []
    for raw in values or []:
        if isinstance(raw, bool):
            raise ContractError(f"{field_name} 必须是正整数")
        try:
            item_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ContractError(f"{field_name} 必须是正整数") from exc
        if item_id <= 0:
            raise ContractError(f"{field_name} 必须是正整数")
        if item_id not in normalized:
            normalized.append(item_id)
    if len(normalized) > max_count:
        raise ContractError(f"{field_name} 最多允许 {max_count} 个")
    return normalized


def _preview_materials(repo: SendContentRepository, content_package: dict[str, Any]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    for material_type, field in (
        ("image", "image_library_ids"),
        ("miniprogram", "miniprogram_library_ids"),
        ("attachment", "attachment_library_ids"),
        ("group_invite", "group_invite_library_ids"),
    ):
        rows = repo.get_materials_by_ids(material_type, list(content_package.get(field) or []))
        materials.extend(
            {
                "type": item["type"],
                "library_id": item["library_id"],
                "title": item.get("title") or "",
                "thumbnail_url": item.get("thumbnail_url") or "",
                "subtitle": item.get("subtitle") or "",
                "enabled": bool(item.get("enabled", True)),
                "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            }
            for item in rows
        )
    return materials
