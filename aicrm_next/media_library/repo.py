from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol

from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.repository_provider import assert_repository_allowed
from aicrm_next.shared.runtime import production_data_ready, raw_database_url

from .variants import (
    THUMBNAIL_SIZE_TO_VARIANT,
    add_image_variant_urls,
    decode_image_base64,
    generate_image_variants,
    make_thumbnail_bytes,
    variant_bytes,
)
from .dto import normalize_group_invite_join_url, normalize_http_url


MEDIA_LIBRARY_BACKEND_ENV = "AICRM_MEDIA_LIBRARY_REPO_BACKEND"
MEDIA_LIBRARY_DATABASE_URL_ENV = "AICRM_MEDIA_LIBRARY_DATABASE_URL"
MEDIA_LIBRARY_SQL_BACKENDS = {"sql", "postgres", "postgresql", "psycopg"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MediaLibraryRepository(Protocol):
    def list_items(self, kind: str, *, limit: int, offset: int, filters: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def list_facets(self, kind: str) -> dict[str, list[str]]: ...
    def get_item(self, kind: str, item_id: str, *, include_data: bool = True) -> dict[str, Any] | None: ...
    def get_image_variant(self, image_id: str, variant_key: str) -> dict[str, Any] | None: ...
    def get_image_thumbnail(self, image_id: str, size: int) -> dict[str, Any] | None: ...
    def save_item(self, kind: str, payload: dict[str, Any], item_id: str | None = None) -> dict[str, Any]: ...
    def delete_item(self, kind: str, item_id: str, *, force: bool = False) -> dict[str, Any]: ...
    def ensure_group_invite_binding(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def claim_group_invite_provisioning(self, item_id: str, claim_token: str, state: str) -> dict[str, Any]: ...
    def store_group_invite_config(self, item_id: str, claim_token: str, config_id: str, state: str) -> dict[str, Any] | None: ...
    def release_group_invite_provisioning(self, item_id: str, claim_token: str) -> None: ...
    def complete_group_invite_provisioning(self, item_id: str, config_id: str, state: str, join_url: str) -> dict[str, Any]: ...
    def reconcile_group_invite_bindings(self, active_chat_ids: list[str], inactive_chat_ids: list[str]) -> int: ...


def normalize_tags(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    raw = value
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    if not isinstance(raw, list):
        raw = [raw]
    tags: list[str] = []
    for item in raw:
        tag = str(item or "").strip()
        if tag and tag not in tags:
            tags.append(tag[:64])
    return tags[:50]


def _extract_base64(data_url: str) -> str:
    if not data_url:
        return ""
    if data_url.startswith("data:"):
        _, _, body = data_url.partition(",")
        return body
    return data_url


def _data_url(data_base64: str, mime_type: str) -> str:
    return f"data:{mime_type or 'application/octet-stream'};base64,{data_base64 or ''}"


def _seed() -> dict[str, list[dict[str, Any]]]:
    ts = "2026-05-20T12:00:00Z"
    return {
        "image": [
            {
                "id": "image_masked_001",
                "name": "商品封面图样例",
                "file_name": "image_masked_001.png",
                "source": "upload",
                "source_url": "",
                "data_base64": "ZmFrZQ==",
                "mime_type": "image/png",
                "content_type": "image/png",
                "file_size": 16,
                "width": 1,
                "height": 1,
                "data_url": "data:image/png;base64,ZmFrZQ==",
                "thumb_media_id": "",
                "thumb_media_id_expires_at": "",
                "enabled": True,
                "description": "",
                "tags": ["commerce"],
                "category": "",
                "ai_metadata": {},
                "created_at": ts,
                "updated_at": ts,
                "deleted": False,
            }
        ],
        "attachment": [
            {
                "id": "attachment_masked_001",
                "name": "附件样例",
                "file_name": "attachment_masked_001.pdf",
                "mime_type": "application/pdf",
                "file_size": 32,
                "data_base64": "ZmFrZQ==",
                "tags": ["fixture"],
                "enabled": True,
                "created_at": ts,
                "updated_at": ts,
                "deleted": False,
            }
        ],
        "miniprogram": [
            {
                "id": "miniprogram_masked_001",
                "name": "小程序卡片样例",
                "title": "小程序卡片样例",
                "appid": "appid_masked_001",
                "pagepath": "pages/masked/index",
                "page_path": "pages/masked/index",
                "thumb_image_id": "image_masked_001",
                "thumb_media_id": "",
                "thumb_image_url": "",
                "thumb_image_base64": "",
                "description": "脱敏小程序素材 fixture",
                "tags": ["fixture"],
                "enabled": True,
                "created_at": ts,
                "updated_at": ts,
                "deleted": False,
            }
        ],
        "group_invite": [
            {
                "id": "group_invite_masked_001",
                "name": "体验群邀请",
                "title": "点击加入体验群",
                "description": "点击卡片进入客户群",
                "pic_url": "",
                "join_url": "https://work.weixin.qq.com/gm/fixture0000000000000000000000000",
                "config_id": "fixture_join_way",
                "state": "fixture",
                "chat_id_list": ["wr_fixture_group"],
                "chat_id": "wr_fixture_group",
                "binding_status": "ready",
                "auto_create_room": True,
                "room_base_name": "体验群",
                "room_base_id": 1,
                "enabled": True,
                "created_at": ts,
                "updated_at": ts,
                "deleted": False,
            }
        ],
    }


class InMemoryMediaLibraryRepository:
    def __init__(self, data: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._data = deepcopy(data if data is not None else _seed())
        self._campaign_steps: list[dict[str, Any]] = []
        self._image_variants: dict[str, dict[str, dict[str, Any]]] = {}

    def list_items(self, kind: str, *, limit: int, offset: int, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = filters or {}
        rows = [deepcopy(item) for item in self._data[kind] if not item.get("deleted")]
        enabled_only = filters.get("enabled_only")
        if enabled_only is not None and bool(enabled_only):
            rows = [item for item in rows if item.get("enabled", True)]
        q = str(filters.get("q") or "").strip().lower()
        if q:
            rows = [item for item in rows if q in self._search_text(kind, item)]
        if kind == "image":
            category = str(filters.get("category") or "").strip()
            if category:
                rows = [item for item in rows if str(item.get("category") or "") == category]
            tags = normalize_tags(filters.get("tags"))
            if tags:
                rows = [item for item in rows if set(normalize_tags(item.get("tags"))) & set(tags)]
            if filters.get("only_unlabeled"):
                rows = [
                    item
                    for item in rows
                    if not item.get("description") or not item.get("category") or not normalize_tags(item.get("tags"))
                ]
        rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        if kind == "image":
            rows = [self._serialize_image(item, include_data=False) for item in rows]
        elif kind == "miniprogram":
            rows = [self._serialize_miniprogram(item) for item in rows]
        return {"items": rows[offset : offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    def list_facets(self, kind: str) -> dict[str, list[str]]:
        categories: set[str] = set()
        tags: set[str] = set()
        for item in self._data[kind]:
            if item.get("deleted") or not item.get("enabled", True):
                continue
            category = item.get("category")
            if isinstance(category, str) and category:
                categories.add(category)
            for tag in normalize_tags(item.get("tags")):
                tags.add(tag)
        return {"categories": sorted(categories), "tags": sorted(tags)}

    def get_item(self, kind: str, item_id: str, *, include_data: bool = True) -> dict[str, Any] | None:
        for item in self._data[kind]:
            if str(item["id"]) == str(item_id) and not item.get("deleted"):
                if kind == "image":
                    return self._serialize_image(item, include_data=include_data)
                if kind == "miniprogram":
                    return self._serialize_miniprogram(item)
                out = deepcopy(item)
                if not include_data:
                    out.pop("data_base64", None)
                return out
        return None

    def get_image_variant(self, image_id: str, variant_key: str) -> dict[str, Any] | None:
        if variant_key not in {"original", "thumb_160", "thumb_320", "preview_720", "mobile_1080", "large_1440"}:
            return None
        item = self.get_item("image", image_id, include_data=True)
        if not item:
            return None
        variants = self._ensure_image_variants(item)
        variant = variants.get(variant_key)
        if not variant:
            return None
        payload = variant_bytes(variant)
        return {**variant, "bytes": payload, "etag": '"' + str(variant.get("checksum") or "") + '"'}

    def get_image_thumbnail(self, image_id: str, size: int) -> dict[str, Any] | None:
        item = self.get_item("image", image_id, include_data=True)
        if not item:
            return None
        if item.get("source_url") and not str(item.get("data_base64") or ""):
            raise ContractError("remote source fetch is disabled in Next media library")
        variant = self.get_image_variant(image_id, THUMBNAIL_SIZE_TO_VARIANT.get(size, ""))
        if variant and str(variant.get("mime_type") or "").split(";")[0] in {"image/png", "image/jpeg"}:
            return variant
        data_base64 = str(item.get("data_base64") or "")
        mime_type = str(item.get("mime_type") or "image/png")
        if data_base64:
            data = decode_image_base64(data_base64)
        elif item.get("source_url"):
            raise ContractError("remote source fetch is disabled in Next media library")
        else:
            data = b""
        return make_thumbnail_bytes(
            image_id=item.get("id") or image_id,
            data=data,
            mime_type=mime_type,
            size=size,
        )

    def save_item(self, kind: str, payload: dict[str, Any], item_id: str | None = None) -> dict[str, Any]:
        now = now_iso()
        if item_id:
            normalized = self._normalize_update_payload(kind, payload)
            if kind == "miniprogram" and normalized.get("thumb_image_id") not in (None, ""):
                self._assert_image_exists(normalized.get("thumb_image_id"))
            for index, item in enumerate(self._data[kind]):
                if str(item["id"]) == str(item_id) and not item.get("deleted"):
                    updated = {**item, **normalized, "id": item["id"], "updated_at": now}
                    self._data[kind][index] = updated
                    if kind == "image":
                        self._image_variants.pop(str(item["id"]), None)
                        return self._serialize_image(updated, include_data=True)
                    if kind == "miniprogram":
                        return self._serialize_miniprogram(updated)
                    return deepcopy(updated)
            raise NotFoundError(f"{kind} item not found")
        normalized = self._normalize_payload(kind, payload)
        if kind == "miniprogram":
            missing = [key for key in ("appid", "pagepath", "title") if not normalized.get(key)]
            if missing:
                raise ContractError("小程序素材缺少必填字段：" + ", ".join(missing))
            if normalized.get("thumb_image_id") not in (None, ""):
                self._assert_image_exists(normalized.get("thumb_image_id"))
        item = {
            **normalized,
            "id": f"{kind}_masked_{len(self._data[kind]) + 1:03d}",
            "created_at": now,
            "updated_at": now,
            "deleted": False,
        }
        self._data[kind].append(item)
        if kind == "image":
            return self._serialize_image(item, include_data=True)
        if kind == "miniprogram":
            return self._serialize_miniprogram(item)
        return deepcopy(item)

    def delete_item(self, kind: str, item_id: str, *, force: bool = False) -> dict[str, Any]:
        index = next((idx for idx, item in enumerate(self._data[kind]) if str(item["id"]) == str(item_id) and not item.get("deleted")), None)
        if index is None:
            raise NotFoundError(f"{kind} item not found")
        if kind == "image":
            refs = self._image_references(item_id)
            if (refs["miniprograms"] or refs["campaign_steps"]) and not force:
                return {"ok": False, "error": "image_has_references", "references": refs}
            cleared = {"miniprograms_cleared": 0, "campaign_steps_cleared": 0}
            if force:
                cleared = self._clear_image_references(item_id)
            del self._data[kind][index]
            return {"ok": True, "deleted": True, "hard_deleted": True, "id": item_id, "references_cleared": cleared}
        del self._data[kind][index]
        return {"ok": True, "deleted": True, "hard_deleted": True, "id": item_id}

    def _search_text(self, kind: str, item: dict[str, Any]) -> str:
        if kind == "miniprogram":
            fields = ["name", "title", "appid", "pagepath", "page_path"]
        elif kind == "attachment":
            fields = ["name", "file_name", "mime_type"]
        elif kind == "group_invite":
            fields = ["name", "title", "description", "join_url", "config_id", "state", "room_base_name"]
        else:
            fields = ["name", "file_name", "description", "category"]
        text = " ".join(str(item.get(field) or "") for field in fields)
        text += " " + " ".join(normalize_tags(item.get("tags")))
        return text.lower()

    def ensure_group_invite_binding(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = str(payload.get("chat_id") or "").strip()
        if not chat_id:
            raise ContractError("chat_id 不能为空")
        for item in self._data["group_invite"]:
            item_chat_id = str(item.get("chat_id") or ((item.get("chat_id_list") or [""])[0])).strip()
            if item_chat_id == chat_id and not item.get("deleted"):
                group_name = str(payload.get("group_name") or "").strip()
                if group_name:
                    item["name"] = group_name
                    item["title"] = f"加入「{group_name}」"
                    item["room_base_name"] = group_name
                item["updated_at"] = now_iso()
                return deepcopy(item)
        group_name = str(payload.get("group_name") or chat_id).strip()
        numeric_ids = [int(item.get("id")) for item in self._data["group_invite"] if str(item.get("id") or "").isdigit()]
        item = {
            "id": max(numeric_ids or [0]) + 1,
            "name": group_name,
            "title": f"加入「{group_name}」",
            "description": "点击卡片直接加入群聊",
            "pic_url": "",
            "join_url": "",
            "config_id": "",
            "state": "",
            "chat_id_list": [chat_id],
            "chat_id": chat_id,
            "binding_status": "pending",
            "auto_create_room": False,
            "room_base_name": group_name,
            "room_base_id": None,
            "enabled": True,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "deleted": False,
        }
        self._data["group_invite"].append(item)
        return deepcopy(item)

    def claim_group_invite_provisioning(self, item_id: str, claim_token: str, state: str) -> dict[str, Any]:
        for item in self._data["group_invite"]:
            if str(item.get("id")) != str(item_id) or item.get("deleted"):
                continue
            current_config_id = str(item.get("config_id") or "").strip()
            can_claim = (
                item.get("binding_status") != "invalid"
                and not str(item.get("join_url") or "").strip()
                and not current_config_id
            )
            if can_claim:
                item["config_id"] = claim_token
                item["state"] = state
                item["updated_at"] = now_iso()
            return {"claimed": can_claim, "item": deepcopy(item)}
        raise NotFoundError("group_invite item not found")

    def store_group_invite_config(self, item_id: str, claim_token: str, config_id: str, state: str) -> dict[str, Any] | None:
        for item in self._data["group_invite"]:
            if str(item.get("id")) != str(item_id) or item.get("deleted"):
                continue
            if str(item.get("config_id") or "") != claim_token:
                return None
            item["config_id"] = config_id
            item["state"] = state
            item["updated_at"] = now_iso()
            return deepcopy(item)
        return None

    def release_group_invite_provisioning(self, item_id: str, claim_token: str) -> None:
        for item in self._data["group_invite"]:
            if str(item.get("id")) == str(item_id) and str(item.get("config_id") or "") == claim_token:
                item["config_id"] = ""
                item["updated_at"] = now_iso()

    def complete_group_invite_provisioning(self, item_id: str, config_id: str, state: str, join_url: str) -> dict[str, Any]:
        normalized_url = normalize_group_invite_join_url(join_url)
        for item in self._data["group_invite"]:
            if str(item.get("id")) != str(item_id) or item.get("deleted"):
                continue
            if str(item.get("config_id") or "") != str(config_id or ""):
                raise ContractError("群邀请自动生成状态已变化，请重试")
            item.update(
                {
                    "config_id": config_id,
                    "state": state,
                    "join_url": normalized_url,
                    "binding_status": "ready",
                    "enabled": True,
                    "updated_at": now_iso(),
                }
            )
            return deepcopy(item)
        raise NotFoundError("group_invite item not found")

    def reconcile_group_invite_bindings(self, active_chat_ids: list[str], inactive_chat_ids: list[str]) -> int:
        active = {str(value or "").strip() for value in active_chat_ids if str(value or "").strip()}
        inactive = {str(value or "").strip() for value in inactive_chat_ids if str(value or "").strip()}
        changed = 0
        for item in self._data["group_invite"]:
            chat_id = str(item.get("chat_id") or ((item.get("chat_id_list") or [""])[0])).strip()
            if chat_id in inactive and item.get("binding_status") != "invalid":
                item["binding_status"] = "invalid"
                item["updated_at"] = now_iso()
                changed += 1
            elif chat_id in active and item.get("binding_status") == "invalid":
                item["binding_status"] = "ready" if str(item.get("join_url") or "").strip() else "pending"
                item["enabled"] = True
                item["updated_at"] = now_iso()
                changed += 1
        return changed

    def _assert_image_exists(self, image_id: Any) -> None:
        if not any(str(item.get("id")) == str(image_id) and not item.get("deleted") for item in self._data["image"]):
            raise ContractError("thumb_image_id 对应的图片素材不存在")

    def _normalize_payload(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = deepcopy(payload)
        if kind == "image":
            mime_type = data.get("mime_type") or data.get("content_type") or "image/png"
            data_base64 = data.get("data_base64")
            if data_base64 is None:
                data_base64 = _extract_base64(str(data.get("data_url") or ""))
            return {
                "name": str(data.get("name") or data.get("file_name") or "图片素材"),
                "file_name": str(data.get("file_name") or "image.png"),
                "source": str(data.get("source") or "upload"),
                "source_url": str(data.get("source_url") or data.get("public_url") or ""),
                "data_base64": str(data_base64 or ""),
                "mime_type": str(mime_type),
                "content_type": str(mime_type),
                "file_size": int(data.get("file_size") or 0),
                "width": int(data.get("width") or 1),
                "height": int(data.get("height") or 1),
                "data_url": str(data.get("data_url") or _data_url(str(data_base64 or ""), str(mime_type))),
                "thumb_media_id": str(data.get("thumb_media_id") or data.get("wecom_media_id") or ""),
                "thumb_media_id_expires_at": str(data.get("thumb_media_id_expires_at") or ""),
                "enabled": bool(data.get("enabled", True)),
                "description": str(data.get("description") or ""),
                "tags": normalize_tags(data.get("tags")),
                "category": str(data.get("category") or ""),
                "ai_metadata": data.get("ai_metadata") if isinstance(data.get("ai_metadata"), dict) else {},
            }
        if kind == "miniprogram":
            pagepath = data.get("pagepath") if data.get("pagepath") is not None else data.get("page_path")
            title = str(data.get("title") or data.get("name") or "")
            return {
                "name": str(data.get("name") or title),
                "title": title,
                "appid": str(data.get("appid") or data.get("app_id") or ""),
                "pagepath": str(pagepath or ""),
                "page_path": str(pagepath or ""),
                "thumb_image_id": data.get("thumb_image_id"),
                "thumb_media_id": str(data.get("thumb_media_id") or ""),
                "thumb_image_url": str(data.get("thumb_image_url") or ""),
                "thumb_image_base64": str(data.get("thumb_image_base64") or ""),
                "description": str(data.get("description") or ""),
                "tags": normalize_tags(data.get("tags")),
                "enabled": bool(data.get("enabled", True)),
            }
        if kind == "group_invite":
            title = str(data.get("title") or data.get("name") or "").strip()
            join_url = normalize_group_invite_join_url(data.get("join_url"))
            if not title or not join_url:
                raise ContractError("群邀请卡片缺少必填字段：title, join_url")
            pic_url = normalize_http_url(data.get("pic_url"), field_name="卡片封面链接")
            description = str(data.get("description") or "").strip()
            if len(title.encode("utf-8")) > 128 or len(description.encode("utf-8")) > 512:
                raise ContractError("群邀请卡片标题或描述超过企微长度限制")
            return {
                "name": str(data.get("name") or title).strip(),
                "title": title,
                "description": description,
                "pic_url": pic_url,
                "join_url": join_url,
                "config_id": str(data.get("config_id") or "").strip(),
                "state": str(data.get("state") or "").strip(),
                "chat_id_list": [str(item).strip() for item in list(data.get("chat_id_list") or []) if str(item).strip()],
                "auto_create_room": bool(data.get("auto_create_room", True)),
                "room_base_name": str(data.get("room_base_name") or "").strip(),
                "room_base_id": int(data["room_base_id"]) if data.get("room_base_id") not in (None, "") else None,
                "enabled": bool(data.get("enabled", True)),
                "chat_id": str(data.get("chat_id") or ((data.get("chat_id_list") or [""])[0])).strip(),
                "binding_status": str(data.get("binding_status") or ("ready" if join_url else "pending")).strip().lower(),
            }
        return {
            "name": str(data.get("name") or data.get("file_name") or "附件素材"),
            "file_name": str(data.get("file_name") or "attachment.bin"),
            "mime_type": str(data.get("mime_type") or "application/octet-stream"),
            "file_size": int(data.get("file_size") or 0),
            "data_base64": str(data.get("data_base64") or ""),
            "tags": normalize_tags(data.get("tags")),
            "enabled": bool(data.get("enabled", True)),
        }

    def _normalize_update_payload(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = deepcopy(payload)
        out: dict[str, Any] = {}
        if kind == "image":
            for key in ["name", "description", "category"]:
                if key in data:
                    out[key] = str(data.get(key) or "")
            if "tags" in data:
                out["tags"] = normalize_tags(data.get("tags"))
            if "enabled" in data:
                out["enabled"] = bool(data.get("enabled"))
            if "ai_metadata" in data:
                out["ai_metadata"] = data.get("ai_metadata") if isinstance(data.get("ai_metadata"), dict) else {}
            return out
        if kind == "miniprogram":
            if "app_id" in data and "appid" not in data:
                data["appid"] = data.get("app_id")
            for key in ["name", "title", "appid"]:
                if key in data:
                    out[key] = str(data.get(key) or "")
            if "pagepath" in data or "page_path" in data:
                pagepath = data.get("pagepath") if data.get("pagepath") is not None else data.get("page_path")
                out["pagepath"] = str(pagepath or "")
                out["page_path"] = str(pagepath or "")
            if "thumb_image_id" in data:
                out["thumb_image_id"] = data.get("thumb_image_id")
                out["thumb_media_id"] = ""
            if "thumb_media_id" in data:
                out["thumb_media_id"] = str(data.get("thumb_media_id") or "")
            if "enabled" in data:
                out["enabled"] = bool(data.get("enabled"))
            return out
        if kind == "group_invite":
            for key in ["name", "title", "description", "config_id", "state", "room_base_name", "chat_id", "binding_status"]:
                if key in data:
                    out[key] = str(data.get(key) or "").strip()
            if "join_url" in data:
                out["join_url"] = normalize_group_invite_join_url(data.get("join_url"))
                out["binding_status"] = "ready" if out["join_url"] else "pending"
            if "pic_url" in data:
                out["pic_url"] = normalize_http_url(data.get("pic_url"), field_name="卡片封面链接")
            if "chat_id_list" in data:
                out["chat_id_list"] = [str(item).strip() for item in list(data.get("chat_id_list") or []) if str(item).strip()]
            if "auto_create_room" in data:
                out["auto_create_room"] = bool(data.get("auto_create_room"))
            if "room_base_id" in data:
                out["room_base_id"] = int(data["room_base_id"]) if data.get("room_base_id") not in (None, "") else None
            if "enabled" in data:
                out["enabled"] = bool(data.get("enabled"))
            if "title" in data and not out.get("title"):
                raise ContractError("群邀请卡片标题不能为空")
            if "join_url" in data and not out.get("join_url"):
                raise ContractError("群邀请链接不能为空")
            return out
        if "name" in data:
            out["name"] = str(data.get("name") or "")
        if "tags" in data:
            out["tags"] = normalize_tags(data.get("tags"))
        if "enabled" in data:
            out["enabled"] = bool(data.get("enabled"))
        return out

    def _ensure_image_variants(self, item: dict[str, Any]) -> dict[str, dict[str, Any]]:
        image_id = str(item.get("id") or "")
        if image_id in self._image_variants:
            return self._image_variants[image_id]
        generated = generate_image_variants(
            image_id=item.get("id") or image_id,
            data_base64=str(item.get("data_base64") or ""),
            mime_type=str(item.get("mime_type") or item.get("content_type") or "image/png"),
        )
        self._image_variants[image_id] = {key: variant.metadata() | {"data_base64": variant.data_base64} for key, variant in generated.items()}
        return self._image_variants[image_id]

    def _serialize_image(self, item: dict[str, Any], *, include_data: bool) -> dict[str, Any]:
        out = deepcopy(item)
        if not include_data:
            out.pop("data_base64", None)
            out.pop("data_url", None)
        variants = self._ensure_image_variants(item)
        original = variants.get("original") or {}
        out["width"] = int(original.get("width") or out.get("width") or 0)
        out["height"] = int(original.get("height") or out.get("height") or 0)
        return add_image_variant_urls(out)

    def _serialize_miniprogram(self, item: dict[str, Any]) -> dict[str, Any]:
        out = deepcopy(item)
        thumb_image_id = out.get("thumb_image_id")
        if thumb_image_id not in (None, ""):
            add_image_variant_urls(out, thumb_image_id)
        return out

    def _image_references(self, image_id: str) -> dict[str, list[dict[str, Any]]]:
        miniprograms = [
            {"id": item["id"], "title": item.get("title") or item.get("name") or ""}
            for item in self._data["miniprogram"]
            if not item.get("deleted") and str(item.get("thumb_image_id") or "") == str(image_id)
        ]
        campaign_steps = [
            {"id": step["id"], "campaign_id": step.get("campaign_id"), "step_index": step.get("step_index")}
            for step in self._campaign_steps
            if str(image_id) in {str(value) for value in step.get("content_payload_json", {}).get("image_library_ids", [])}
        ]
        return {"miniprograms": miniprograms, "campaign_steps": campaign_steps}

    def _clear_image_references(self, image_id: str) -> dict[str, int]:
        miniprograms_cleared = 0
        for item in self._data["miniprogram"]:
            if str(item.get("thumb_image_id") or "") == str(image_id):
                item["thumb_image_id"] = None
                item["thumb_media_id"] = ""
                item["updated_at"] = now_iso()
                miniprograms_cleared += 1
        campaign_steps_cleared = 0
        for step in self._campaign_steps:
            ids = step.get("content_payload_json", {}).get("image_library_ids", [])
            next_ids = [value for value in ids if str(value) != str(image_id)]
            if next_ids != ids:
                step["content_payload_json"]["image_library_ids"] = next_ids
                step["updated_at"] = now_iso()
                campaign_steps_cleared += 1
        return {"miniprograms_cleared": miniprograms_cleared, "campaign_steps_cleared": campaign_steps_cleared}


_GLOBAL_REPO = InMemoryMediaLibraryRepository()


def build_media_library_repository() -> MediaLibraryRepository:
    backend = str(os.getenv(MEDIA_LIBRARY_BACKEND_ENV, "") or "").strip().lower()
    if production_data_ready() or backend in MEDIA_LIBRARY_SQL_BACKENDS:
        database_url = str(os.getenv(MEDIA_LIBRARY_DATABASE_URL_ENV, "") or "").strip() or raw_database_url()
        if not database_url:
            raise ContractError(f"{MEDIA_LIBRARY_DATABASE_URL_ENV} or DATABASE_URL is required for media library Postgres repository")
        from .postgres_repo import PostgresMediaLibraryRepository

        return assert_repository_allowed(PostgresMediaLibraryRepository(database_url), capability_owner="media_library")
    return assert_repository_allowed(_GLOBAL_REPO, capability_owner="media_library")


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def connect_media_library_db(database_url: str) -> Any:
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_psycopg_url(database_url), row_factory=dict_row)


def reset_media_library_fixture_state() -> None:
    global _GLOBAL_REPO
    _GLOBAL_REPO = InMemoryMediaLibraryRepository()
