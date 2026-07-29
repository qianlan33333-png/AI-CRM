from __future__ import annotations

from typing import Any, Protocol

from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.wecom_payload_contract import normalize_miniprogram_attachment_payload

from .dto import normalize_group_invite_join_url, normalize_http_url
from .repo import MediaLibraryRepository, build_media_library_repository


class BroadcastMaterialPlanPort(Protocol):
    def plan(self, content_package: dict[str, Any]) -> dict[str, Any]: ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ids(value: Any, *, limit: int) -> list[int]:
    result: list[int] = []
    for item in list(value or []) if isinstance(value, (list, tuple)) else []:
        if isinstance(item, bool):
            continue
        try:
            item_id = int(item)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in result:
            result.append(item_id)
        if len(result) >= limit:
            break
    return result


class RepositoryBroadcastMaterialPlanPort:
    """Build a provider-free media dependency plan for one private message."""

    def __init__(self, repository: MediaLibraryRepository) -> None:
        self._repository = repository

    def plan(self, content_package: dict[str, Any]) -> dict[str, Any]:
        package = content_package if isinstance(content_package, dict) else {}
        attachments: list[dict[str, Any]] = []
        uploads: dict[str, dict[str, Any]] = {}

        for material_id in _ids(package.get("image_library_ids"), limit=3):
            self._require_upload_source("image", material_id)
            material_key = self._add_upload(uploads, "image", material_id, "image")
            attachments.append({"msgtype": "image", "image": {"media_dependency_key": material_key}})

        for material_id in _ids(package.get("miniprogram_library_ids"), limit=1):
            item = self._item("miniprogram", material_id)
            payload = normalize_miniprogram_attachment_payload(
                {
                    "appid": item.get("appid") or item.get("app_id"),
                    "page": item.get("pagepath") or item.get("page_path"),
                    "title": item.get("title") or item.get("name"),
                    "pic_media_id": "dependency",
                }
            )
            self._require_miniprogram_source(material_id, item)
            material_key = self._add_upload(uploads, "miniprogram", material_id, "image")
            payload.pop("pic_media_id", None)
            payload["pic_media_dependency_key"] = material_key
            attachments.append({"msgtype": "miniprogram", "miniprogram": payload})

        for material_id in _ids(package.get("attachment_library_ids"), limit=9):
            self._require_upload_source("attachment", material_id)
            material_key = self._add_upload(uploads, "attachment", material_id, "attachment")
            attachments.append({"msgtype": "file", "file": {"media_dependency_key": material_key}})

        for material_id in _ids(package.get("group_invite_library_ids"), limit=1):
            attachments.append(self._group_invite(material_id))

        dynamic_card = package.get("dynamic_miniprogram_card")
        if isinstance(dynamic_card, dict) and dynamic_card:
            cover_image_id = int(dynamic_card.get("cover_image_id") or 0)
            if cover_image_id <= 0:
                raise ContractError("dynamic_card_cover_image_id_missing")
            self._require_upload_source("image", cover_image_id)
            payload = normalize_miniprogram_attachment_payload(
                {
                    "appid": dynamic_card.get("appid"),
                    "page": dynamic_card.get("pagepath") or dynamic_card.get("page"),
                    "title": dynamic_card.get("title"),
                    "pic_media_id": "dependency",
                }
            )
            material_key = self._add_upload(uploads, "image", cover_image_id, "image")
            payload.pop("pic_media_id", None)
            payload["pic_media_dependency_key"] = material_key
            attachments.append({"msgtype": "miniprogram", "miniprogram": payload})

        if len(attachments) > 9:
            raise ContractError("private_message_attachments_exceed_limit")
        return {"attachments": attachments, "uploads": list(uploads.values())}

    def _item(self, kind: str, material_id: int) -> dict[str, Any]:
        item = self._repository.get_item(kind, str(material_id), include_data=True)
        if not item:
            raise ContractError(f"{kind}_material_not_found:id={material_id}")
        if item.get("enabled") is False:
            raise ContractError(f"{kind}_material_disabled:id={material_id}")
        return dict(item)

    def _require_upload_source(self, kind: str, material_id: int) -> dict[str, Any]:
        item = self._item(kind, material_id)
        if not _text(item.get("data_base64")):
            raise ContractError(f"{kind}_material_source_missing:id={material_id}")
        return item

    def _require_miniprogram_source(self, material_id: int, item: dict[str, Any]) -> None:
        thumb_image_id = item.get("thumb_image_id")
        if thumb_image_id not in (None, ""):
            self._require_upload_source("image", int(thumb_image_id))
            return
        if not _text(item.get("thumb_image_base64")):
            raise ContractError(f"miniprogram_material_source_missing:id={material_id}")

    def _group_invite(self, material_id: int) -> dict[str, Any]:
        item = self._item("group_invite", material_id)
        binding_status = _text(item.get("binding_status") or ("ready" if item.get("join_url") else "pending"))
        if binding_status != "ready":
            raise ContractError(f"group_invite_not_ready:id={material_id}:{binding_status}")
        title = _text(item.get("title") or item.get("name"))
        join_url = normalize_group_invite_join_url(item.get("join_url"))
        if not title or not join_url:
            raise ContractError(f"group_invite_not_ready:id={material_id}:incomplete")
        link: dict[str, Any] = {"title": title, "url": join_url}
        description = _text(item.get("description"))
        if description:
            link["desc"] = description
        pic_url = normalize_http_url(item.get("pic_url"), field_name="卡片封面链接") if _text(item.get("pic_url")) else ""
        if pic_url:
            link["picurl"] = pic_url
        return {"msgtype": "link", "link": link}

    @staticmethod
    def _add_upload(
        uploads: dict[str, dict[str, Any]],
        material_kind: str,
        material_id: int,
        upload_kind: str,
    ) -> str:
        material_key = f"{material_kind}:{material_id}:{upload_kind}"
        uploads.setdefault(
            material_key,
            {
                "material_key": material_key,
                "material_kind": material_kind,
                "material_id": int(material_id),
                "upload_kind": upload_kind,
            },
        )
        return material_key


def build_broadcast_material_plan_port() -> BroadcastMaterialPlanPort:
    return RepositoryBroadcastMaterialPlanPort(build_media_library_repository())


__all__ = [
    "BroadcastMaterialPlanPort",
    "RepositoryBroadcastMaterialPlanPort",
    "build_broadcast_material_plan_port",
]
