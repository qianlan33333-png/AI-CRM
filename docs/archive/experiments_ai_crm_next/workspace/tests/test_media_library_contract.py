from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.engagement.media_library.postgres_repo import PostgresMediaLibraryRepository


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdac`\x00\x01\x00\x00\x07\x00\x01\xe9\x15\x08-"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_client() -> TestClient:
    return TestClient(create_app())


class _CursorResultOverwriteProbe:
    def __init__(self) -> None:
        self._rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        if "count(*) AS total" in sql:
            self._rows = [{"total": 1}]
            return
        if "SELECT * FROM image_library" in sql:
            self._rows = [
                {
                    "id": 101,
                    "name": "生产素材",
                    "file_name": "asset.png",
                    "source": "upload",
                    "source_url": "",
                    "mime_type": "image/png",
                    "file_size": 123,
                    "thumb_media_id": "",
                    "thumb_media_id_expires_at": None,
                    "enabled": True,
                    "description": "",
                    "tags": [],
                    "category": "",
                    "ai_metadata": {},
                    "width": 0,
                    "height": 0,
                    "created_at": "",
                    "updated_at": "",
                }
            ]
            return
        if "to_regclass('public.image_library_variants')" in sql:
            self._rows = [{"table_name": None}]
            return
        self._rows = []

    def fetchone(self) -> dict | None:
        if not self._rows:
            return None
        return self._rows.pop(0)

    def fetchall(self) -> list[dict]:
        rows = list(self._rows)
        self._rows = []
        return rows


class _ConnectionResultOverwriteProbe:
    def __init__(self, cursor: _CursorResultOverwriteProbe) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> _CursorResultOverwriteProbe:
        return self._cursor


def test_postgres_list_fetches_rows_before_variant_probe_overwrites_cursor() -> None:
    cursor = _CursorResultOverwriteProbe()
    repo = PostgresMediaLibraryRepository("postgresql://unused")
    repo._connect = lambda: _ConnectionResultOverwriteProbe(cursor)  # type: ignore[method-assign]

    payload = repo.list_items("image", limit=5, offset=0, filters={"enabled_only": True})

    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == [101]


def test_image_library_frontend_contract_filters_upload_update_and_delete() -> None:
    client = make_client()

    uploaded = client.post(
        "/api/admin/image-library/upload",
        files={"image": ("hero.png", BytesIO(TINY_PNG), "image/png")},
        data={"name": "活动主图", "description": "五月活动", "tags": "活动, 海报", "category": "活动海报"},
    ).json()["item"]

    filtered = client.get(
        "/api/admin/image-library",
        params={
            "enabled_only": "true",
            "q": "活动",
            "category": "活动海报",
            "tags": "海报",
            "only_unlabeled": "false",
        },
    ).json()
    assert filtered["ok"] is True
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == uploaded["id"]
    for key in [
        "id",
        "name",
        "file_name",
        "source",
        "source_url",
        "mime_type",
        "file_size",
        "thumb_media_id",
        "thumb_media_id_expires_at",
        "enabled",
        "description",
        "tags",
        "category",
        "ai_metadata",
        "thumb_url",
        "thumb_160_url",
        "thumb_320_url",
        "preview_url",
        "width",
        "height",
        "created_at",
        "updated_at",
    ]:
        assert key in filtered["items"][0]
    assert "data_base64" not in filtered["items"][0]

    facets = client.get("/api/admin/image-library/facets").json()
    assert facets["ok"] is True
    assert "活动海报" in facets["categories"]
    assert "海报" in facets["tags"]

    detail = client.get(f"/api/admin/image-library/{uploaded['id']}").json()
    assert detail["ok"] is True
    assert "data_base64" not in detail["item"]

    detail_with_data = client.get(f"/api/admin/image-library/{uploaded['id']}?include_data=true").json()
    assert detail_with_data["ok"] is True
    assert detail_with_data["item"]["data_base64"]

    updated = client.put(
        f"/api/admin/image-library/{uploaded['id']}",
        json={"description": "更新描述", "tags": ["转化"], "category": "好评截图", "enabled": False},
    ).json()["item"]
    assert updated["description"] == "更新描述"
    assert updated["tags"] == ["转化"]
    assert updated["category"] == "好评截图"
    assert updated["enabled"] is False

    unlabeled = client.get("/api/admin/image-library", params={"only_unlabeled": "true", "enabled_only": "true"}).json()
    assert any(item["id"] == "image_masked_001" for item in unlabeled["items"])

    conflict = client.delete("/api/admin/image-library/image_masked_001")
    assert conflict.status_code == 409
    assert conflict.json()["references"]["miniprograms"]

    forced = client.delete("/api/admin/image-library/image_masked_001?force=true").json()
    assert forced["ok"] is True
    assert forced["references_cleared"]["miniprograms_cleared"] == 1


def test_miniprogram_library_accepts_pagepath_aliases_and_test_resolve() -> None:
    client = make_client()
    created = client.post(
        "/api/admin/miniprogram-library",
        json={
            "name": "体验课卡片",
            "appid": "wx_fixture",
            "pagepath": "pages/index?from=crm",
            "title": "预约体验课",
            "thumb_image_id": "image_masked_001",
            "enabled": True,
        },
    ).json()["item"]
    assert created["pagepath"] == "pages/index?from=crm"
    assert "page_path" in created
    assert "thumb_320_url" in created

    updated = client.put(
        f"/api/admin/miniprogram-library/{created['id']}",
        json={"page_path": "pages/detail?id=1", "title": "更新标题"},
    ).json()["item"]
    assert updated["pagepath"] == "pages/detail?id=1"
    assert updated["page_path"] == "pages/detail?id=1"

    listed = client.get("/api/admin/miniprogram-library?enabled_only=false").json()
    assert listed["ok"] is True
    assert any(item["pagepath"] == "pages/detail?id=1" for item in listed["items"])

    resolved = client.post(f"/api/admin/miniprogram-library/{created['id']}/test-resolve").json()
    assert resolved["ok"] is True
    assert resolved["thumb_media_id"]

    deleted = client.delete(f"/api/admin/miniprogram-library/{created['id']}").json()
    assert deleted["ok"] is True
    assert deleted["deleted"] is True
    assert deleted["hard_deleted"] is True
    assert deleted["id"] == str(created["id"])
    assert deleted["real_external_call_executed"] is False


def test_miniprogram_library_accepts_page_path_and_rejects_missing_thumb_json() -> None:
    client = make_client()
    created = client.post(
        "/api/admin/miniprogram-library",
        json={
            "name": "page_path alias",
            "appid": "wx_fixture",
            "page_path": "pages/alias/index",
            "title": "别名路径",
            "thumb_image_id": "image_masked_001",
        },
    )
    assert created.status_code == 200
    assert created.json()["item"]["pagepath"] == "pages/alias/index"

    missing_thumb = client.post(
        "/api/admin/miniprogram-library",
        json={
            "name": "bad thumb",
            "appid": "wx_fixture",
            "pagepath": "pages/index",
            "title": "坏缩略图",
            "thumb_image_id": "999999",
        },
    )
    assert missing_thumb.headers.get("content-type", "").startswith("application/json")
    assert missing_thumb.status_code in {200, 400}
    body = missing_thumb.json()
    assert body["ok"] is False
    assert "不存在" in body["error"] or "not found" in body["error"]


def test_attachment_library_upload_list_detail_update_and_delete() -> None:
    client = make_client()
    created = client.post(
        "/api/admin/attachment-library/upload",
        files={"attachment": ("guide.pdf", BytesIO(b"%PDF-1.4\n%%EOF\n"), "application/pdf")},
        data={"name": "欢迎资料", "tags": "欢迎语,PDF"},
    ).json()["item"]
    assert created["file_name"] == "guide.pdf"
    assert created["tags"] == ["欢迎语", "PDF"]

    listed = client.get("/api/admin/attachment-library", params={"q": "欢迎", "enabled_only": "true"}).json()
    assert listed["ok"] is True
    assert listed["total"] == 1
    for key in ["id", "name", "file_name", "mime_type", "file_size", "tags", "enabled", "created_at", "updated_at"]:
        assert key in listed["items"][0]

    detail = client.get(f"/api/admin/attachment-library/{created['id']}").json()
    assert detail["ok"] is True
    assert detail["item"]["data_base64"]

    updated = client.put(
        f"/api/admin/attachment-library/{created['id']}",
        json={"name": "欢迎资料更新", "tags": ["课前"], "enabled": False},
    ).json()["item"]
    assert updated["name"] == "欢迎资料更新"
    assert updated["tags"] == ["课前"]
    assert updated["enabled"] is False

    deleted = client.delete(f"/api/admin/attachment-library/{created['id']}").json()
    assert deleted["ok"] is True
    assert deleted["deleted"] is True
    assert deleted["hard_deleted"] is True
    assert deleted["id"] == str(created["id"])
    assert deleted["real_external_call_executed"] is False
