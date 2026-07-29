from __future__ import annotations

from aicrm_next.engagement.media_library.application import TestResolveMiniprogramThumbCommand as ResolveMiniprogramThumbCommand
from aicrm_next.engagement.media_library.application import UpsertMediaItemCommand
from aicrm_next.engagement.media_library.repo import InMemoryMediaLibraryRepository


PNG_1PX_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAEAAAcAAekVCC0AAAAASUVORK5CYII="


def _empty_repo() -> InMemoryMediaLibraryRepository:
    return InMemoryMediaLibraryRepository({"image": [], "attachment": [], "miniprogram": []})


def test_miniprogram_save_binds_cached_image_library_media_id() -> None:
    repo = _empty_repo()
    image = repo.save_item(
        "image",
        {
            "name": "课程封面",
            "file_name": "lesson.png",
            "data_base64": PNG_1PX_BASE64,
            "mime_type": "image/png",
            "thumb_media_id": "media-real-cover-001",
        },
    )

    result = UpsertMediaItemCommand("miniprogram", repo)(
        {
            "name": "日课卡片",
            "appid": "wx-course",
            "pagepath": "pages/article/article?lesson_id=lesson-1&from=learn",
            "title": "黄小璨的一封信",
            "thumb_image_id": image["id"],
        }
    )

    assert result["ok"] is True
    assert result["thumb_resolve"]["source"] == "image_library_cache"
    assert result["item"]["thumb_image_id"] == image["id"]
    assert result["item"]["thumb_media_id"] == "media-real-cover-001"


def test_production_thumb_resolve_does_not_fallback_to_fake_media(monkeypatch) -> None:
    import aicrm_next.engagement.media_library.application as app_module

    repo = _empty_repo()
    image = repo.save_item(
        "image",
        {
            "name": "未上传封面",
            "file_name": "lesson.png",
            "data_base64": PNG_1PX_BASE64,
            "mime_type": "image/png",
        },
    )
    miniprogram = repo.save_item(
        "miniprogram",
        {
            "name": "日课卡片",
            "appid": "wx-course",
            "pagepath": "pages/article/article?lesson_id=lesson-2&from=learn",
            "title": "黄小璨的一封信",
            "thumb_image_id": image["id"],
        },
    )

    monkeypatch.setenv("AICRM_NEXT_ENV", "production")

    class FakeAdapterShouldNotRun:
        def upload_image(self, **kwargs):
            raise AssertionError("production resolve must not fallback to fake Next media adapter")

    monkeypatch.setattr(app_module, "build_wecom_media_adapter", lambda: FakeAdapterShouldNotRun())

    result = ResolveMiniprogramThumbCommand(repo)(str(miniprogram["id"]))

    assert result["ok"] is False
    assert result["error"] == "real_wecom_media_resolve_failed"
    assert "thumb_image_id" in result
