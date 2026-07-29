from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from aicrm_next.automation.automation_engine.group_ops.material_resolver import (
    GroupOpsMaterialResolveError,
    InMemoryGroupOpsMaterialResolver,
)


FUTURE = "2099-01-01T00:00:00+00:00"
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


class RecordingUploader:
    def __init__(self) -> None:
        self.image_calls: list[dict] = []
        self.attachment_calls: list[dict] = []

    def upload_image(self, file_name: str, file_bytes: bytes, content_type: str) -> dict:
        self.image_calls.append({"file_name": file_name, "file_bytes": file_bytes, "content_type": content_type})
        return {"errcode": 0, "media_id": "media_uploaded_image"}

    def upload_attachment(self, file_name: str, file_bytes: bytes, content_type: str) -> dict:
        self.attachment_calls.append({"file_name": file_name, "file_bytes": file_bytes, "content_type": content_type})
        return {"errcode": 0, "media_id": "media_uploaded_attachment"}


def _resolver(
    items: dict[str, dict[int, dict]],
    *,
    uploader: RecordingUploader | None = None,
    real_upload_enabled: bool = False,
    fake_media_enabled: bool = True,
) -> InMemoryGroupOpsMaterialResolver:
    return InMemoryGroupOpsMaterialResolver(
        items,
        uploader=uploader,
        real_upload_enabled=real_upload_enabled,
        fake_media_enabled=fake_media_enabled,
        now=NOW,
    )


def _items() -> dict[str, dict[int, dict]]:
    return {
        "image": {
            12: {
                "id": 12,
                "enabled": True,
                "file_name": "hero.png",
                "mime_type": "image/png",
                "thumb_media_id": "media_img_cached",
                "thumb_media_id_expires_at": FUTURE,
            },
            13: {"id": 13, "enabled": True, "file_name": "second.png", "mime_type": "image/png"},
            14: {"id": 14, "enabled": True, "file_name": "third.png", "mime_type": "image/png"},
            15: {"id": 15, "enabled": True, "file_name": "fourth.png", "mime_type": "image/png"},
        },
        "miniprogram": {
            34: {
                "id": 34,
                "enabled": True,
                "appid": "wx_app_001",
                "pagepath": "pages/index",
                "title": "Mini Card",
                "thumb_media_id": "media_mini_thumb",
                "thumb_media_id_expires_at": FUTURE,
            },
            35: {
                "id": 35,
                "enabled": True,
                "appid": "wx_app_002",
                "pagepath": "pages/detail",
                "title": "Mini With Image",
                "thumb_image_id": 12,
            },
        },
        "attachment": {
            56: {
                "id": 56,
                "enabled": True,
                "file_name": "guide.pdf",
                "mime_type": "application/pdf",
                "media_id": "media_file_cached",
                "media_id_expires_at": FUTURE,
            },
        },
        "group_invite": {
            78: {
                "id": 78,
                "enabled": True,
                "title": "点击加入体验群",
                "description": "进群领取资料",
                "join_url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef",
                "pic_url": "https://example.com/group-cover.png",
            },
        },
    }


def test_group_ops_material_resolver_image_uses_cached_media_id() -> None:
    uploader = RecordingUploader()

    attachments, image_media_ids = _resolver(_items(), uploader=uploader).resolve_content_package_materials(
        {"image_library_ids": [12]}
    )

    assert attachments == []
    assert image_media_ids == ["media_img_cached"]
    assert uploader.image_calls == []


def test_group_ops_material_resolver_image_fake_upload_when_no_cached_media() -> None:
    items = _items()
    items["image"][12].pop("thumb_media_id")
    uploader = RecordingUploader()

    _, image_media_ids = _resolver(items, uploader=uploader).resolve_content_package_materials({"image_library_ids": [12]})

    assert image_media_ids[0].startswith("fake_group_ops_image_12_")
    assert uploader.image_calls == []


def test_group_ops_material_resolver_image_missing_fails() -> None:
    with pytest.raises(GroupOpsMaterialResolveError) as exc_info:
        _resolver(_items()).resolve_content_package_materials({"image_library_ids": [404]})

    assert "image_library_resolve_failed:id=404" in str(exc_info.value)


def test_group_ops_material_resolver_miniprogram_materializes_attachment() -> None:
    uploader = RecordingUploader()

    attachments, image_media_ids = _resolver(_items(), uploader=uploader).resolve_content_package_materials({"miniprogram_library_ids": [34]})

    assert image_media_ids == []
    assert uploader.image_calls == []
    assert attachments == [
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx_app_001",
                "page": "pages/index",
                "title": "Mini Card",
                "pic_media_id": "media_mini_thumb",
            },
        }
    ]
    assert "pagepath" not in attachments[0]["miniprogram"]
    assert "thumb_media_id" not in attachments[0]["miniprogram"]


def test_group_ops_material_resolver_group_invite_materializes_link_without_upload() -> None:
    uploader = RecordingUploader()

    attachments, image_media_ids = _resolver(_items(), uploader=uploader).resolve_content_package_materials(
        {"group_invite_library_ids": [78]}
    )

    assert image_media_ids == []
    assert uploader.image_calls == []
    assert uploader.attachment_calls == []
    assert attachments == [
        {
            "msgtype": "link",
            "link": {
                "title": "点击加入体验群",
                "url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef",
                "desc": "进群领取资料",
                "picurl": "https://example.com/group-cover.png",
            },
        }
    ]


def test_group_ops_material_resolver_miniprogram_expired_media_uploads_and_caches() -> None:
    items = _items()
    items["miniprogram"][34]["thumb_media_id"] = "expired-media"
    items["miniprogram"][34]["thumb_media_id_expires_at"] = "2020-01-01T00:00:00+00:00"
    items["miniprogram"][34]["thumb_image_base64"] = "aW1hZ2UtYnl0ZXM="
    uploader = RecordingUploader()

    attachments, image_media_ids = _resolver(
        items,
        uploader=uploader,
        real_upload_enabled=True,
        fake_media_enabled=False,
    ).resolve_content_package_materials({"miniprogram_library_ids": [34]})

    assert image_media_ids == []
    assert uploader.image_calls == [
        {"file_name": "miniprogram_thumb_34.png", "file_bytes": b"image-bytes", "content_type": "image/png"}
    ]
    assert items["miniprogram"][34]["thumb_media_id"] == "media_uploaded_image"
    assert items["miniprogram"][34]["thumb_media_id_expires_at"]
    assert attachments[0]["miniprogram"] == {
        "appid": "wx_app_001",
        "page": "pages/index",
        "title": "Mini Card",
        "pic_media_id": "media_uploaded_image",
    }


def test_group_ops_material_resolver_miniprogram_base64_uploads_when_no_cached_media() -> None:
    items = _items()
    items["miniprogram"][34]["thumb_media_id"] = ""
    items["miniprogram"][34]["thumb_media_id_expires_at"] = ""
    items["miniprogram"][34]["thumb_image_base64"] = "dGh1bWItYnl0ZXM="
    uploader = RecordingUploader()

    attachment = _resolver(
        items,
        uploader=uploader,
        real_upload_enabled=True,
        fake_media_enabled=False,
    ).resolve_wecom_ready_miniprogram(34)

    assert uploader.image_calls[0]["file_bytes"] == b"thumb-bytes"
    assert items["miniprogram"][34]["thumb_media_id"] == "media_uploaded_image"
    assert attachment["miniprogram"]["pic_media_id"] == "media_uploaded_image"


def test_group_ops_material_resolver_miniprogram_upload_disabled_fails() -> None:
    items = _items()
    items["miniprogram"][34]["thumb_media_id"] = ""
    items["miniprogram"][34]["thumb_media_id_expires_at"] = ""
    items["miniprogram"][34]["thumb_image_base64"] = "dGh1bWItYnl0ZXM="

    with pytest.raises(GroupOpsMaterialResolveError) as exc_info:
        _resolver(items, fake_media_enabled=False).resolve_content_package_materials({"miniprogram_library_ids": [34]})

    assert str(exc_info.value) == "miniprogram_resolve_failed:id=34:real_upload_not_enabled"


def test_group_ops_material_resolver_miniprogram_thumb_image_id_reuses_image_resolver() -> None:
    attachments, _ = _resolver(_items()).resolve_content_package_materials({"miniprogram_library_ids": [35]})

    assert attachments[0]["miniprogram"]["pic_media_id"] == "media_img_cached"


def test_group_ops_material_resolver_miniprogram_missing_required_fields_fails() -> None:
    items = _items()
    items["miniprogram"][34]["appid"] = ""

    with pytest.raises(GroupOpsMaterialResolveError) as exc_info:
        _resolver(items).resolve_content_package_materials({"miniprogram_library_ids": [34]})

    assert "miniprogram_resolve_failed:id=34" in str(exc_info.value)


def test_group_ops_material_resolver_attachment_materializes_file() -> None:
    attachments, image_media_ids = _resolver(_items()).resolve_content_package_materials({"attachment_library_ids": [56]})

    assert image_media_ids == []
    assert attachments == [{"msgtype": "file", "file": {"media_id": "media_file_cached"}}]


def test_group_ops_material_resolver_attachment_missing_or_disabled_fails() -> None:
    items = _items()
    items["attachment"][56]["enabled"] = False

    with pytest.raises(GroupOpsMaterialResolveError) as exc_info:
        _resolver(items).resolve_content_package_materials({"attachment_library_ids": [56]})

    assert "attachment_resolve_failed:id=56" in str(exc_info.value)

    with pytest.raises(GroupOpsMaterialResolveError) as missing_exc:
        _resolver(_items()).resolve_content_package_materials({"attachment_library_ids": [404]})

    assert "attachment_resolve_failed:id=404" in str(missing_exc.value)


def test_group_ops_material_resolver_limits_and_dedupes_ids() -> None:
    items = _items()
    for item_id in range(57, 68):
        items["attachment"][item_id] = {
            "id": item_id,
            "enabled": True,
            "media_id": f"media_file_{item_id}",
            "media_id_expires_at": FUTURE,
        }

    attachments, image_media_ids = _resolver(items).resolve_content_package_materials(
        {
            "image_library_ids": [12, 12, 13, 14, 15],
            "miniprogram_library_ids": [34, 35],
            "attachment_library_ids": [56, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65],
        }
    )

    assert len(image_media_ids) == 3
    assert image_media_ids == ["media_img_cached", image_media_ids[1], image_media_ids[2]]
    assert attachments[0]["msgtype"] == "miniprogram"
    assert attachments[0]["miniprogram"]["appid"] == "wx_app_001"
    assert len([item for item in attachments if item["msgtype"] == "file"]) == 9


def test_group_ops_material_resolver_no_legacy_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    gateway_text = (root / "aicrm_next/automation/automation_engine/group_ops/integration_gateway.py").read_text(encoding="utf-8")
    resolver_text = (root / "aicrm_next/automation/automation_engine/group_ops/material_resolver.py").read_text(encoding="utf-8")

    assert "wecom_ability" + "_service" not in gateway_text
    assert "legacy_flask_facade" not in gateway_text
    assert "wecom_ability" + "_service" not in resolver_text
    assert "legacy_flask_facade" not in resolver_text
