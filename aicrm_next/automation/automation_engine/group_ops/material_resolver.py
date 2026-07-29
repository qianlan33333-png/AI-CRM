from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from aicrm_next.automation.automation_engine.group_ops.message_content import normalize_miniprogram_attachment_payload
from aicrm_next.integration_ports import build_wecom_media_upload_client
from aicrm_next.engagement.media_library.postgres_repo import PostgresMediaLibraryRepository
from aicrm_next.engagement.media_library.dto import normalize_group_invite_join_url, normalize_http_url
from aicrm_next.engagement.send_content.repo import InMemorySendContentRepository
from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.runtime import production_data_ready, production_environment, raw_database_url


JsonDict = dict[str, Any]
MEDIA_CACHE_TTL = timedelta(days=2)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _enabled(item: JsonDict) -> bool:
    value = item.get("enabled", True)
    if isinstance(value, bool):
        return value
    return _text(value).lower() not in {"0", "false", "no", "off"}


def _int_ids(values: Any, *, limit: int) -> list[int]:
    result: list[int] = []
    raw_values = values if isinstance(values, (list, tuple, set)) else [values]
    for value in raw_values or []:
        try:
            item = int(value or 0)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _text(value)
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _cached_media_id(item: JsonDict, media_key: str, expires_key: str, *, now: datetime) -> str:
    media_id = _text(item.get(media_key))
    if not media_id:
        return ""
    expires_at = _parse_datetime(item.get(expires_key))
    if not expires_at:
        return ""
    if expires_at.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
        return ""
    return media_id


def _decode_base64(value: Any) -> bytes:
    text = _text(value)
    if not text:
        return b""
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    return base64.b64decode(text)


def _fake_media_id(kind: str, item_id: int, item: JsonDict) -> str:
    digest_source = "|".join(
        [
            kind,
            str(item_id),
            _text(item.get("file_name")),
            _text(item.get("name")),
            _text(item.get("data_base64"))[:64],
        ]
    )
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:12]
    return f"fake_group_ops_{kind}_{item_id}_{digest}"


def _expiry(now: datetime) -> datetime:
    return now.astimezone(timezone.utc) + MEDIA_CACHE_TTL


class GroupOpsMaterialResolveError(ContractError):
    pass


class GroupOpsMaterialResolver(Protocol):
    def resolve_content_package_materials(
        self,
        content_package: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        ...

    def resolve_wecom_ready_miniprogram(self, material_id: int) -> dict[str, Any]:
        ...


class _BaseGroupOpsMaterialResolver:
    def __init__(
        self,
        *,
        uploader: Any = None,
        real_upload_enabled: bool = False,
        fake_media_enabled: bool = False,
        now: datetime | None = None,
    ) -> None:
        self._uploader = uploader
        self._real_upload_enabled = real_upload_enabled
        self._fake_media_enabled = fake_media_enabled
        self._now = now

    def resolve_content_package_materials(
        self,
        content_package: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        attachments: list[dict[str, Any]] = []
        image_media_ids: list[str] = []
        package = content_package if isinstance(content_package, dict) else {}

        for image_id in _int_ids(package.get("image_library_ids"), limit=3):
            image_media_ids.append(self._resolve_image_media_id(image_id))

        for miniprogram_id in _int_ids(package.get("miniprogram_library_ids"), limit=1):
            attachments.append(self._resolve_miniprogram_attachment(miniprogram_id))

        for attachment_id in _int_ids(package.get("attachment_library_ids"), limit=9):
            attachments.append(self._resolve_file_attachment(attachment_id))

        for group_invite_id in _int_ids(package.get("group_invite_library_ids"), limit=1):
            attachments.append(self._resolve_group_invite_attachment(group_invite_id))

        return attachments, image_media_ids

    def resolve_wecom_ready_miniprogram(self, material_id: int) -> dict[str, Any]:
        return self._resolve_miniprogram_attachment(int(material_id))

    def _now_utc(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def _get_item(self, kind: str, item_id: int) -> JsonDict | None:
        raise NotImplementedError

    def _cache_image_media_id(self, item_id: int, media_id: str, expires_at: datetime) -> None:
        return None

    def _cache_miniprogram_thumb_media_id(self, item_id: int, media_id: str, expires_at: datetime) -> None:
        return None

    def _cache_attachment_media_id(self, item_id: int, media_id: str, expires_at: datetime) -> None:
        return None

    def _error(self, prefix: str, item_id: int, reason: Any) -> GroupOpsMaterialResolveError:
        return GroupOpsMaterialResolveError(f"{prefix}:id={item_id}:{reason}")

    def _uploader_client(self) -> Any:
        if self._uploader is None:
            self._uploader = build_wecom_media_upload_client()
        return self._uploader

    def _resolve_image_media_id(self, image_id: int) -> str:
        item = self._get_item("image", image_id)
        if not item:
            raise self._error("image_library_resolve_failed", image_id, "not_found")
        if not _enabled(item):
            raise self._error("image_library_resolve_failed", image_id, "disabled")

        now = self._now_utc()
        cached = _cached_media_id(item, "thumb_media_id", "thumb_media_id_expires_at", now=now)
        if cached:
            return cached
        cached = _cached_media_id(item, "media_id", "media_id_expires_at", now=now)
        if cached:
            return cached

        if self._fake_media_enabled:
            return _fake_media_id("image", image_id, item)
        if not self._real_upload_enabled:
            raise self._error("image_library_resolve_failed", image_id, "real_upload_not_enabled")

        payload = _decode_base64(item.get("data_base64"))
        if not payload:
            raise self._error("image_library_resolve_failed", image_id, "missing_image_data")
        try:
            uploaded = self._uploader_client().upload_image(
                _text(item.get("file_name")) or f"image_{image_id}.png",
                payload,
                _text(item.get("mime_type")) or _text(item.get("content_type")) or "image/png",
            )
            media_id = _text(uploaded.get("media_id"))
        except Exception as exc:
            raise self._error("image_library_resolve_failed", image_id, exc) from exc
        if not media_id:
            raise self._error("image_library_resolve_failed", image_id, "empty_media_id")
        self._cache_image_media_id(image_id, media_id, _expiry(now))
        return media_id

    def _resolve_miniprogram_attachment(self, miniprogram_id: int) -> dict[str, Any]:
        item = self._get_item("miniprogram", miniprogram_id)
        if not item:
            raise self._error("miniprogram_resolve_failed", miniprogram_id, "not_found")
        if not _enabled(item):
            raise self._error("miniprogram_resolve_failed", miniprogram_id, "disabled")

        appid = _text(item.get("appid") or item.get("app_id"))
        pagepath = _text(item.get("pagepath") or item.get("page_path"))
        title = _text(item.get("title") or item.get("name"))
        missing = [name for name, value in (("appid", appid), ("pagepath", pagepath), ("title", title)) if not value]
        if missing:
            raise self._error("miniprogram_resolve_failed", miniprogram_id, "missing_" + ",".join(missing))

        now = self._now_utc()
        thumb_media_id = _cached_media_id(item, "thumb_media_id", "thumb_media_id_expires_at", now=now)
        if not thumb_media_id and item.get("thumb_image_id") not in (None, ""):
            try:
                thumb_media_id = self._resolve_image_media_id(int(item.get("thumb_image_id") or 0))
            except Exception as exc:
                raise self._error("miniprogram_resolve_failed", miniprogram_id, exc) from exc
            if thumb_media_id:
                self._cache_miniprogram_thumb_media_id(miniprogram_id, thumb_media_id, _expiry(now))
        if not thumb_media_id and _text(item.get("thumb_image_base64")):
            thumb_media_id = self._upload_miniprogram_thumb(miniprogram_id, item, now)
        if not thumb_media_id and _text(item.get("thumb_image_url")):
            raise self._error("miniprogram_resolve_failed", miniprogram_id, "thumb_image_url_fetch_disabled")
        if not thumb_media_id:
            raise self._error("miniprogram_resolve_failed", miniprogram_id, "missing_thumb_media_id")

        return {
            "msgtype": "miniprogram",
            "miniprogram": normalize_miniprogram_attachment_payload(
                {
                    "appid": appid,
                    "pagepath": pagepath,
                    "title": title,
                    "thumb_media_id": thumb_media_id,
                }
            ),
        }

    def _upload_miniprogram_thumb(self, miniprogram_id: int, item: JsonDict, now: datetime) -> str:
        if self._fake_media_enabled:
            media_id = _fake_media_id("miniprogram_thumb", miniprogram_id, item)
        else:
            if not self._real_upload_enabled:
                raise self._error("miniprogram_resolve_failed", miniprogram_id, "real_upload_not_enabled")
            payload = _decode_base64(item.get("thumb_image_base64"))
            if not payload:
                raise self._error("miniprogram_resolve_failed", miniprogram_id, "missing_thumb_image_data")
            try:
                uploaded = self._uploader_client().upload_image(
                    f"miniprogram_thumb_{miniprogram_id}.png",
                    payload,
                    "image/png",
                )
                media_id = _text(uploaded.get("media_id"))
            except Exception as exc:
                raise self._error("miniprogram_resolve_failed", miniprogram_id, exc) from exc
        if not media_id:
            raise self._error("miniprogram_resolve_failed", miniprogram_id, "empty_thumb_media_id")
        self._cache_miniprogram_thumb_media_id(miniprogram_id, media_id, _expiry(now))
        return media_id

    def _resolve_file_attachment(self, attachment_id: int) -> dict[str, Any]:
        item = self._get_item("attachment", attachment_id)
        if not item:
            raise self._error("attachment_resolve_failed", attachment_id, "not_found")
        if not _enabled(item):
            raise self._error("attachment_resolve_failed", attachment_id, "disabled")

        now = self._now_utc()
        media_id = _cached_media_id(item, "media_id", "media_id_expires_at", now=now)
        if not media_id:
            if self._fake_media_enabled:
                media_id = _fake_media_id("attachment", attachment_id, item)
            elif self._real_upload_enabled:
                media_id = self._upload_file_attachment(attachment_id, item, now)
            else:
                raise self._error("attachment_resolve_failed", attachment_id, "real_upload_not_enabled")

        return {"msgtype": "file", "file": {"media_id": media_id}}

    def _resolve_group_invite_attachment(self, group_invite_id: int) -> dict[str, Any]:
        item = self._get_item("group_invite", group_invite_id)
        if not item:
            raise self._error("group_invite_not_ready", group_invite_id, "not_found")
        if not _enabled(item):
            raise self._error("group_invite_not_ready", group_invite_id, "disabled")
        binding_status = _text(item.get("binding_status") or ("ready" if item.get("join_url") else "pending"))
        if binding_status != "ready":
            raise self._error("group_invite_not_ready", group_invite_id, binding_status or "pending")
        title = _text(item.get("title") or item.get("name"))
        if not title:
            raise self._error("group_invite_not_ready", group_invite_id, "missing_title")
        try:
            join_url = normalize_group_invite_join_url(item.get("join_url"))
            pic_url = normalize_http_url(item.get("pic_url"), field_name="卡片封面链接") if _text(item.get("pic_url")) else ""
        except ValueError as exc:
            raise self._error("group_invite_not_ready", group_invite_id, exc) from exc
        if not join_url:
            raise self._error("group_invite_not_ready", group_invite_id, "missing_join_url")
        link: dict[str, Any] = {"title": title, "url": join_url}
        if _text(item.get("description")):
            link["desc"] = _text(item.get("description"))
        if pic_url:
            link["picurl"] = pic_url
        return {"msgtype": "link", "link": link}

    def _upload_file_attachment(self, attachment_id: int, item: JsonDict, now: datetime) -> str:
        payload = _decode_base64(item.get("data_base64"))
        if not payload:
            raise self._error("attachment_resolve_failed", attachment_id, "missing_attachment_data")
        try:
            uploaded = self._uploader_client().upload_attachment(
                _text(item.get("file_name")) or f"attachment_{attachment_id}.bin",
                payload,
                _text(item.get("mime_type")) or "application/octet-stream",
            )
            media_id = _text(uploaded.get("media_id"))
        except Exception as exc:
            raise self._error("attachment_resolve_failed", attachment_id, exc) from exc
        if not media_id:
            raise self._error("attachment_resolve_failed", attachment_id, "empty_media_id")
        self._cache_attachment_media_id(attachment_id, media_id, _expiry(now))
        return media_id


class InMemoryGroupOpsMaterialResolver(_BaseGroupOpsMaterialResolver):
    def __init__(
        self,
        items: dict[str, dict[int, JsonDict]] | None = None,
        *,
        uploader: Any = None,
        real_upload_enabled: bool = False,
        fake_media_enabled: bool = True,
        now: datetime | None = None,
    ) -> None:
        super().__init__(
            uploader=uploader,
            real_upload_enabled=real_upload_enabled,
            fake_media_enabled=fake_media_enabled,
            now=now,
        )
        self._items = items if items is not None else self._default_items()

    def _default_items(self) -> dict[str, dict[int, JsonDict]]:
        repository = InMemorySendContentRepository()
        result: dict[str, dict[int, JsonDict]] = {"image": {}, "miniprogram": {}, "attachment": {}, "group_invite": {}}
        for kind in result:
            materials = repository.get_materials_by_ids(kind, [12, 34, 56, 78])
            for item in materials:
                normalized = dict(item.get("metadata") or {})
                normalized.update(item)
                result[kind][int(item.get("library_id") or item.get("id") or 0)] = normalized
        return result

    def _get_item(self, kind: str, item_id: int) -> JsonDict | None:
        item = (self._items.get(kind) or {}).get(int(item_id))
        return dict(item) if item else None

    def _cache_image_media_id(self, item_id: int, media_id: str, expires_at: datetime) -> None:
        item = (self._items.get("image") or {}).get(int(item_id))
        if item is not None:
            item["thumb_media_id"] = media_id
            item["thumb_media_id_expires_at"] = expires_at.isoformat()

    def _cache_miniprogram_thumb_media_id(self, item_id: int, media_id: str, expires_at: datetime) -> None:
        item = (self._items.get("miniprogram") or {}).get(int(item_id))
        if item is not None:
            item["thumb_media_id"] = media_id
            item["thumb_media_id_expires_at"] = expires_at.isoformat()

    def _cache_attachment_media_id(self, item_id: int, media_id: str, expires_at: datetime) -> None:
        item = (self._items.get("attachment") or {}).get(int(item_id))
        if item is not None:
            item["media_id"] = media_id
            item["media_id_expires_at"] = expires_at.isoformat()


class PostgresGroupOpsMaterialResolver(_BaseGroupOpsMaterialResolver):
    def __init__(
        self,
        media_repository: PostgresMediaLibraryRepository,
        *,
        uploader: Any = None,
        real_upload_enabled: bool = True,
        now: datetime | None = None,
        lease_manager: Any = None,
    ) -> None:
        super().__init__(uploader=uploader, real_upload_enabled=real_upload_enabled, fake_media_enabled=False, now=now)
        self._media_repository = media_repository
        if lease_manager is None and real_upload_enabled:
            from aicrm_next.engagement.media_library.wecom_lease import WeComMediaLeaseManager

            lease_manager = WeComMediaLeaseManager(media_repository, uploader=uploader, now=now)
        self._lease_manager = lease_manager

    def _get_item(self, kind: str, item_id: int) -> JsonDict | None:
        return self._media_repository.get_item(kind, str(item_id), include_data=True)

    def _cache_image_media_id(self, item_id: int, media_id: str, expires_at: datetime) -> None:
        self._media_repository.cache_image_media_id(str(item_id), media_id, expires_at)

    def _cache_miniprogram_thumb_media_id(self, item_id: int, media_id: str, expires_at: datetime) -> None:
        self._media_repository.cache_miniprogram_thumb_media_id(str(item_id), media_id, expires_at)

    def _cache_attachment_media_id(self, item_id: int, media_id: str, expires_at: datetime) -> None:
        self._media_repository.cache_attachment_media_id(str(item_id), media_id, expires_at)

    def _resolve_image_media_id(self, image_id: int) -> str:
        if self._lease_manager is None:
            return super()._resolve_image_media_id(image_id)
        try:
            return _text(self._lease_manager.ensure_ready("image", image_id, upload_kind="image").get("media_id"))
        except Exception as exc:
            raise self._error("image_library_resolve_failed", image_id, exc) from exc

    def _resolve_file_attachment(self, attachment_id: int) -> dict[str, Any]:
        if self._lease_manager is None:
            return super()._resolve_file_attachment(attachment_id)
        try:
            media_id = _text(
                self._lease_manager.ensure_ready("attachment", attachment_id, upload_kind="attachment").get("media_id")
            )
        except Exception as exc:
            raise self._error("attachment_resolve_failed", attachment_id, exc) from exc
        return {"msgtype": "file", "file": {"media_id": media_id}}

    def _resolve_miniprogram_attachment(self, miniprogram_id: int) -> dict[str, Any]:
        if self._lease_manager is None:
            return super()._resolve_miniprogram_attachment(miniprogram_id)
        item = self._get_item("miniprogram", miniprogram_id)
        if not item or not _enabled(item):
            raise self._error("miniprogram_resolve_failed", miniprogram_id, "not_found_or_disabled")
        appid = _text(item.get("appid") or item.get("app_id"))
        pagepath = _text(item.get("pagepath") or item.get("page_path"))
        title = _text(item.get("title") or item.get("name"))
        if not all((appid, pagepath, title)):
            raise self._error("miniprogram_resolve_failed", miniprogram_id, "missing_required_fields")
        try:
            media_id = _text(
                self._lease_manager.ensure_ready("miniprogram", miniprogram_id, upload_kind="image").get("media_id")
            )
        except Exception as exc:
            raise self._error("miniprogram_resolve_failed", miniprogram_id, exc) from exc
        return {
            "msgtype": "miniprogram",
            "miniprogram": normalize_miniprogram_attachment_payload(
                {"appid": appid, "pagepath": pagepath, "title": title, "thumb_media_id": media_id}
            ),
        }


def build_group_ops_material_resolver() -> GroupOpsMaterialResolver:
    if production_data_ready():
        return PostgresGroupOpsMaterialResolver(
            PostgresMediaLibraryRepository(raw_database_url()),
            real_upload_enabled=True,
        )
    if production_environment():
        raise GroupOpsMaterialResolveError("group_ops_material_resolver_production_data_not_ready")
    return InMemoryGroupOpsMaterialResolver(real_upload_enabled=False)
