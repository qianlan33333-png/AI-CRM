from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aicrm_next.crm.customer_read_model import api as customer_read_model_api
from aicrm_next.crm.customer_read_model import extension_port as sidebar_extension_port
from aicrm_next.crm.customer_read_model import sidebar_v2
from aicrm_next.engagement.media_library.postgres_repo import PostgresMediaLibraryRepository
from aicrm_next.engagement.media_library.repo import InMemoryMediaLibraryRepository
from aicrm_next.extensions.commerce.service_period import sidebar_extension_adapter
from aicrm_next.main import create_app
from aicrm_next.platform.admin_config.application_support import _validate_known_setting
from aicrm_next.engagement.media_library.application import GetImageThumbnailQuery
from aicrm_next.platform.shared.errors import ContractError, NotFoundError
from tests.sidebar_auth_test_helpers import install_sidebar_auth


def _image_item(**overrides: Any) -> dict[str, Any]:
    return {
        "id": 42,
        "name": "内部文件名.jpg",
        "file_name": "internal.jpg",
        "description": "每周复盘封面",
        "category": "运营",
        "tags": ["每周复盘", "AI外部Campaign"],
        "enabled": True,
        "updated_at": "2026-07-30T08:00:00+08:00",
        **overrides,
    }


def test_sidebar_material_search_passes_query_and_returns_configured_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeListMediaItemsQuery:
        def __init__(self, kind: str) -> None:
            captured["kind"] = kind

        def __call__(self, *, limit: int, offset: int, filters: dict[str, Any]) -> dict[str, Any]:
            captured.update({"limit": limit, "offset": offset, "filters": filters})
            return {"items": [_image_item()]}

    monkeypatch.setattr(sidebar_v2, "ListMediaItemsQuery", FakeListMediaItemsQuery)
    monkeypatch.setattr(
        sidebar_v2,
        "runtime_setting",
        lambda key, default="": "每周复盘，直播\n深度咨询,每周复盘",
    )

    payload = sidebar_v2.SidebarMaterialReadModel()(material_type="image", limit=50, q="  每周复盘  ")

    assert captured == {
        "kind": "image",
        "limit": 50,
        "offset": 0,
        "filters": {"enabled_only": True, "q": "每周复盘"},
    }
    assert payload["quick_keywords"] == ["每周复盘", "直播", "深度咨询"]
    assert payload["materials"][0]["thumbnail_url"] == (
        "/api/sidebar/v2/materials/image/42/thumbnail?v=2026-07-30T08%3A00%3A00%2B08%3A00"
    )


@pytest.mark.parametrize("configured", ["", "一个", "一个,两个", "一个,一个,两个", "一,二,三,四,五,六"])
def test_sidebar_material_quick_keywords_hide_invalid_or_empty_runtime_values(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setattr(sidebar_v2, "runtime_setting", lambda key, default="": configured)

    assert sidebar_v2._sidebar_image_quick_keywords() == []


def test_sidebar_material_quick_keyword_setting_validates_and_normalizes() -> None:
    assert _validate_known_setting("AICRM_SIDEBAR_IMAGE_QUICK_KEYWORDS", "") == ""
    assert _validate_known_setting("AICRM_SIDEBAR_IMAGE_QUICK_KEYWORDS", " 复盘，直播\n咨询,复盘 ") == "复盘,直播,咨询"
    with pytest.raises(ValueError, match="3 至 5"):
        _validate_known_setting("AICRM_SIDEBAR_IMAGE_QUICK_KEYWORDS", "复盘,直播")
    with pytest.raises(ValueError, match="3 至 5"):
        _validate_known_setting("AICRM_SIDEBAR_IMAGE_QUICK_KEYWORDS", "一,二,三,四,五,六")


def test_postgres_image_keyword_search_covers_metadata_and_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = PostgresMediaLibraryRepository("postgresql://example.invalid/db")
    captured: dict[str, Any] = {}

    def fake_select_list(
        kind: str,
        table: str,
        where: list[str],
        params: list[Any],
        order_by: str,
        *,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        captured.update({"kind": kind, "table": table, "where": where, "params": params})
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    monkeypatch.setattr(repo, "_select_list", fake_select_list)

    repo._list_images(limit=50, offset=0, filters={"enabled_only": True, "q": "复盘"})

    query = " ".join(captured["where"])
    assert "name ILIKE" in query
    assert "file_name ILIKE" in query
    assert "description ILIKE" in query
    assert "category ILIKE" in query
    assert "jsonb_array_elements_text(tags)" in query
    assert captured["params"] == ["%复盘%"] * 5


@pytest.mark.parametrize("query", ["内部文件名", "internal.jpg", "复盘封面", "运营", "ai外部campaign"])
def test_in_memory_image_keyword_search_matches_all_supported_fields(query: str) -> None:
    repo = InMemoryMediaLibraryRepository(
        {"image": [_image_item(deleted=False)], "attachment": [], "miniprogram": []}
    )

    result = repo.list_items("image", limit=50, offset=0, filters={"enabled_only": True, "q": query})

    assert [item["id"] for item in result["items"]] == [42]


def test_remote_only_thumbnail_returns_trusted_https_redirect_without_fetch() -> None:
    repo = InMemoryMediaLibraryRepository(
        {
            "image": [
                _image_item(
                    id="remote-only",
                    source_url="https://cdn.example.com/material.png",
                    data_base64="",
                    mime_type="image/png",
                    deleted=False,
                )
            ],
            "attachment": [],
            "miniprogram": [],
        }
    )

    thumbnail = repo.get_image_thumbnail("remote-only", 160)

    assert thumbnail == {
        "redirect_url": "https://cdn.example.com/material.png",
        "mime_type": "image/png",
    }


def test_remote_only_thumbnail_rejects_non_https_source() -> None:
    repo = InMemoryMediaLibraryRepository(
        {
            "image": [
                _image_item(
                    id="remote-http",
                    source_url="http://cdn.example.com/material.png",
                    data_base64="",
                    mime_type="image/png",
                    deleted=False,
                )
            ],
            "attachment": [],
            "miniprogram": [],
        }
    )

    with pytest.raises(ContractError, match="remote source fetch is disabled"):
        repo.get_image_thumbnail("remote-http", 160)


def test_public_thumbnail_query_rejects_disabled_material() -> None:
    repo = InMemoryMediaLibraryRepository(
        {
            "image": [
                _image_item(
                    id="disabled-image",
                    enabled=False,
                    source_url="https://cdn.example.com/disabled.png",
                    data_base64="",
                    deleted=False,
                )
            ],
            "attachment": [],
            "miniprogram": [],
        }
    )

    with pytest.raises(NotFoundError, match="image item not found"):
        GetImageThumbnailQuery(repo)("disabled-image", 160, enabled_only=True)

    assert GetImageThumbnailQuery(repo)("disabled-image", 160)["thumbnail"]["redirect_url"] == (
        "https://cdn.example.com/disabled.png"
    )


def test_postgres_public_thumbnail_filters_disabled_source_and_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[str] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, sql: str, params: tuple[Any, ...]) -> None:
            statements.append(" ".join(sql.split()))

        def fetchone(self):
            return None

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return FakeCursor()

    repo = PostgresMediaLibraryRepository("postgresql://example.invalid/db")
    repo._variants_table_available = True
    monkeypatch.setattr(repo, "_connect", lambda: FakeConnection())

    assert repo.get_image_thumbnail("42", 160, enabled_only=True) is None
    assert any("JOIN image_library i ON i.id = v.image_id" in sql and "i.enabled IS TRUE" in sql for sql in statements)
    assert any("FROM image_library WHERE id = %s AND enabled IS TRUE" in sql for sql in statements)


def test_sidebar_thumbnail_read_model_requests_enabled_material_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeGetImageThumbnailQuery:
        def __call__(self, image_id: str, size: int, *, enabled_only: bool = False) -> dict[str, Any]:
            captured.update({"image_id": image_id, "size": size, "enabled_only": enabled_only})
            return {
                "thumbnail": {
                    "bytes": b"image-bytes",
                    "mime_type": "image/png",
                    "etag": '"enabled-image"',
                }
            }

    monkeypatch.setattr(sidebar_v2, "GetImageThumbnailQuery", FakeGetImageThumbnailQuery)

    result = sidebar_v2.SidebarMaterialReadModel().thumbnail(42)

    assert captured == {"image_id": "42", "size": 160, "enabled_only": True}
    assert result["body"] == b"image-bytes"


def test_sidebar_extension_thumbnail_query_forwards_enabled_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakePort:
        def get_image_thumbnail(
            self,
            image_id: str,
            size: int,
            *,
            enabled_only: bool = False,
        ) -> dict[str, Any]:
            captured.update({"image_id": image_id, "size": size, "enabled_only": enabled_only})
            return {"thumbnail": {"bytes": b"image-bytes"}}

    monkeypatch.setattr(sidebar_extension_port, "_FACTORY", lambda: FakePort())

    result = sidebar_extension_port.GetImageThumbnailQuery()("42", 160, enabled_only=True)

    assert captured == {"image_id": "42", "size": 160, "enabled_only": True}
    assert result["thumbnail"]["bytes"] == b"image-bytes"


def test_default_sidebar_extension_adapter_forwards_enabled_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeGetImageThumbnailQuery:
        def __call__(
            self,
            image_id: str,
            size: int,
            *,
            enabled_only: bool = False,
        ) -> dict[str, Any]:
            captured.update({"image_id": image_id, "size": size, "enabled_only": enabled_only})
            return {"thumbnail": {"bytes": b"image-bytes"}}

    monkeypatch.setattr(sidebar_extension_adapter, "GetImageThumbnailQuery", FakeGetImageThumbnailQuery)

    result = sidebar_extension_adapter.DefaultSidebarExtensionAdapter().get_image_thumbnail(
        "42",
        160,
        enabled_only=True,
    )

    assert captured == {"image_id": "42", "size": 160, "enabled_only": True}
    assert result["thumbnail"]["bytes"] == b"image-bytes"


def test_sidebar_material_route_forwards_image_query_with_authenticated_context(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeSidebarMaterialReadModel:
        def __call__(self, *, material_type: str, limit: int, q: str) -> dict[str, Any]:
            captured.update({"material_type": material_type, "limit": limit, "q": q})
            return {"material_type": material_type, "materials": [], "quick_keywords": []}

    monkeypatch.setattr(customer_read_model_api, "SidebarMaterialReadModel", FakeSidebarMaterialReadModel)
    client = TestClient(create_app(), raise_server_exceptions=False)
    client.headers.update(install_sidebar_auth(client, viewer_userid="owner-1", external_userid="external-1"))

    response = client.get("/api/sidebar/v2/materials", params={"type": "image", "limit": 50, "q": "每周复盘"})

    assert response.status_code == 200
    assert response.json()["route_owner"] == "ai_crm_next"
    assert captured == {"material_type": "image", "limit": 50, "q": "每周复盘"}


def test_sidebar_material_route_rejects_unauthenticated_search() -> None:
    response = TestClient(create_app(), raise_server_exceptions=False).get(
        "/api/sidebar/v2/materials",
        params={"type": "image", "q": "每周复盘"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "sidebar context required"}


def test_sidebar_thumbnail_route_returns_https_redirect_without_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSidebarMaterialReadModel:
        def thumbnail(self, image_id: int) -> dict[str, Any]:
            assert image_id == 42
            return {"redirect_url": "https://cdn.example.com/material.png", "mime_type": "image/png"}

    monkeypatch.setattr(customer_read_model_api, "SidebarMaterialReadModel", FakeSidebarMaterialReadModel)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/sidebar/v2/materials/image/42/thumbnail?v=1", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://cdn.example.com/material.png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_sidebar_thumbnail_route_honors_etag_for_local_image(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSidebarMaterialReadModel:
        def thumbnail(self, image_id: int) -> dict[str, Any]:
            assert image_id == 42
            return {"body": b"image-bytes", "mime_type": "image/png", "etag": '"image-v1"'}

    monkeypatch.setattr(customer_read_model_api, "SidebarMaterialReadModel", FakeSidebarMaterialReadModel)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(
        "/api/sidebar/v2/materials/image/42/thumbnail?v=1",
        headers={"If-None-Match": '"image-v1"'},
    )

    assert response.status_code == 304
    assert response.headers["etag"] == '"image-v1"'
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_sidebar_thumbnail_route_uses_bounded_cache_without_version(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSidebarMaterialReadModel:
        def thumbnail(self, image_id: int) -> dict[str, Any]:
            assert image_id == 42
            return {"body": b"image-bytes", "mime_type": "image/png", "etag": '"image-v1"'}

    monkeypatch.setattr(customer_read_model_api, "SidebarMaterialReadModel", FakeSidebarMaterialReadModel)

    response = TestClient(create_app(), raise_server_exceptions=False).get(
        "/api/sidebar/v2/materials/image/42/thumbnail"
    )

    assert response.status_code == 200
    assert response.content == b"image-bytes"
    assert response.headers["cache-control"] == "public, max-age=86400"
