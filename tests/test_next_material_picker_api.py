from __future__ import annotations

import pytest


@pytest.fixture()
def client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from aicrm_next.main import create_app

    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_validate_returns_route_owner_and_normalized_package(client) -> None:
    response = client.post(
        "/api/admin/send-content/validate",
        json={
            "content_package": {
                "content_text": "  你好  ",
                "image_library_ids": [12, "12", 13],
                "miniprogram_library_ids": [34],
                "attachment_library_ids": [56],
                "group_invite_library_ids": [78],
            }
        },
    )

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.json() == {
        "ok": True,
        "content_package": {
            "content_text": "你好",
            "image_library_ids": [12, 13],
            "miniprogram_library_ids": [34],
            "attachment_library_ids": [56],
            "group_invite_library_ids": [78],
        },
    }


def test_validate_error_is_json_not_html(client) -> None:
    response = client.post(
        "/api/admin/send-content/validate",
        json={"content_package": {"image_library_ids": ["abc"]}},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    body = response.json()
    assert body["ok"] is False
    assert "正整数" in body["error"]


def test_preview_is_local_only_and_does_not_create_tasks(client, monkeypatch) -> None:
    import requests

    def _fail_external_call(*args, **kwargs):
        raise AssertionError("preview must not perform external HTTP calls")

    monkeypatch.setattr(requests, "post", _fail_external_call)
    retired_before = client.get("/api/admin/automation-conversion/tasks")
    assert retired_before.status_code == 404

    response = client.post(
        "/api/admin/send-content/preview",
        json={
            "content_package": {
                "content_text": "  预览  ",
                "image_library_ids": [12],
                "miniprogram_library_ids": [34],
                "attachment_library_ids": [56],
                "group_invite_library_ids": [78],
            }
        },
    )

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    body = response.json()
    assert body["ok"] is True
    assert body["preview"]["content_text"] == "预览"
    assert body["preview"]["material_summary"] == {
        "image_count": 1,
        "miniprogram_count": 1,
        "attachment_count": 1,
        "group_invite_count": 1,
    }
    assert all("media_id" not in item for item in body["preview"]["materials"])
    retired_after = client.get("/api/admin/automation-conversion/tasks")
    assert retired_after.status_code == 404


def test_material_picker_image_shape_excludes_base64(client) -> None:
    response = client.get("/api/admin/material-picker/items?type=image")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    item = response.json()["items"][0]
    assert {"type", "library_id", "title", "thumbnail_url", "enabled", "metadata"} <= set(item)
    assert item["type"] == "image"
    assert "data_base64" not in item
    assert "data_base64" not in item["metadata"]


def test_material_picker_miniprogram_shape(client) -> None:
    response = client.get("/api/admin/material-picker/items?type=miniprogram")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["type"] == "miniprogram"
    assert item["metadata"]["appid"]
    assert item["metadata"]["pagepath"]


def test_material_picker_attachment_shape(client) -> None:
    response = client.get("/api/admin/material-picker/items?type=attachment")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["type"] == "attachment"
    assert {"file_name", "mime_type", "file_size"} <= set(item["metadata"])
    assert item["mime_type"] == "application/pdf"
    assert item["metadata"]["mime_type"] == "application/pdf"


def test_material_picker_group_invite_shape(client) -> None:
    response = client.get("/api/admin/material-picker/items?type=group_invite")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["type"] == "group_invite"
    assert item["metadata"]["join_url"].startswith("https://work.weixin.qq.com/gm/")
    assert item["title"]


def test_material_picker_unknown_type_returns_400_json(client) -> None:
    response = client.get("/api/admin/material-picker/items?type=unknown")

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.json()["ok"] is False


def test_material_assets_read_model_unifies_library_sources(client) -> None:
    response = client.get("/api/admin/material-assets?limit=10")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    body = response.json()
    assert body["ok"] is True
    assert body["read_model"] == "material_assets"
    assets = body["assets"]
    assert {item["asset_type"] for item in assets} == {"image", "miniprogram", "attachment", "group_invite"}
    assert {item["source_table"] for item in assets} == {"image_library", "miniprogram_library", "attachment_library", "group_invite_library"}
    assert all(item["material_asset_id"] == f"{item['asset_type']}:{item['source_id']}" for item in assets)
    assert all("data_base64" not in item for item in assets)
    assert {"next_cursor", "has_more", "sort_key", "source_cursor"} <= set(body)
    assert body["sort_key"] == "asset_type_order:source_offset"


def test_material_assets_can_filter_to_one_source_type(client) -> None:
    response = client.get("/api/admin/material-assets?type=miniprogram")

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "miniprogram"
    assert body["assets"]
    assert {item["asset_type"] for item in body["assets"]} == {"miniprogram"}


def test_material_assets_single_type_preserves_offset(client) -> None:
    response = client.get("/api/admin/material-assets?type=image&offset=1&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "image"
    assert body["offset"] == 1
    assert body["limit"] == 1
    assert [item["material_asset_id"] for item in body["assets"]] == ["image:13"]
    assert body["total"] == 2


def test_material_assets_all_type_fetches_enough_rows_before_unified_slice(client) -> None:
    response = client.get("/api/admin/material-assets?type=all&offset=3&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "all"
    assert body["offset"] == 3
    assert body["limit"] == 1
    assert [item["material_asset_id"] for item in body["assets"]] == ["attachment:56"]
    assert body["total"] == 5


def test_material_assets_all_type_deep_offset_stays_inside_large_source(client, monkeypatch) -> None:
    import aicrm_next.engagement.send_content.application as app_module

    repo = _LargeMaterialAssetsRepository()
    monkeypatch.setattr(app_module, "build_send_content_repository", lambda: repo)

    response = client.get("/api/admin/material-assets?type=all&offset=100&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "all"
    assert body["offset"] == 100
    assert body["limit"] == 1
    assert [item["material_asset_id"] for item in body["assets"]] == ["image:101"]
    assert body["total"] == 103


def test_material_assets_cursor_continues_inside_large_source(client, monkeypatch) -> None:
    import aicrm_next.engagement.send_content.application as app_module

    repo = _LargeMaterialAssetsRepository()
    monkeypatch.setattr(app_module, "build_send_content_repository", lambda: repo)

    first_page = client.get("/api/admin/material-assets?type=all&limit=100")
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["assets"]) == 100
    assert first_body["has_more"] is True
    assert first_body["source_cursor"] == {"material_type": "image", "source_index": 0, "offset": 100}

    second_page = client.get(f"/api/admin/material-assets?type=all&limit=1&cursor={first_body['next_cursor']}")

    assert second_page.status_code == 200
    second_body = second_page.json()
    assert [item["material_asset_id"] for item in second_body["assets"]] == ["image:101"]
    assert second_body["has_more"] is True
    assert second_body["source_cursor"] == {"material_type": "miniprogram", "source_index": 1, "offset": 0}


def test_material_assets_cursor_moves_to_next_source_after_current_source(client) -> None:
    first_page = client.get("/api/admin/material-assets?type=all&limit=2")
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert [item["material_asset_id"] for item in first_body["assets"]] == ["image:12", "image:13"]
    assert first_body["source_cursor"] == {"material_type": "miniprogram", "source_index": 1, "offset": 0}

    second_page = client.get(f"/api/admin/material-assets?type=all&limit=2&cursor={first_body['next_cursor']}")

    assert second_page.status_code == 200
    second_body = second_page.json()
    assert [item["material_asset_id"] for item in second_body["assets"]] == ["miniprogram:34", "attachment:56"]
    assert second_body["has_more"] is True
    assert second_body["next_cursor"]

    third_page = client.get(f"/api/admin/material-assets?type=all&limit=2&cursor={second_body['next_cursor']}")
    assert third_page.status_code == 200
    third_body = third_page.json()
    assert [item["material_asset_id"] for item in third_body["assets"]] == ["group_invite:78"]
    assert third_body["has_more"] is False


def test_material_assets_rejects_invalid_cursor(client) -> None:
    response = client.get("/api/admin/material-assets?type=all&cursor=not-a-valid-cursor")

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "游标" in response.json()["error"]


def test_material_asset_usage_lineage_lists_business_consumers(client) -> None:
    response = client.get("/api/admin/material-assets/image:12/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["read_model"] == "material_asset_usage"
    assert body["material_asset_id"] == "image:12"
    assert body["asset_type"] == "image"
    assert body["source_id"] == 12
    assert body["total"] >= 3
    assert {"channel_welcome_config", "cloud_plan_content_payload", "wechat_pay_product_page_slice"} <= {
        item["consumer_type"] for item in body["usage"]
    }
    assert all(item["material_asset_id"] == "image:12" for item in body["usage"])
    assert all("data_base64" not in str(item) for item in body["usage"])


def test_material_asset_usage_lineage_supports_other_material_types(client) -> None:
    miniprogram_response = client.get("/api/admin/material-assets/miniprogram:34/usage")
    attachment_response = client.get("/api/admin/material-assets/attachment:56/usage")

    assert miniprogram_response.status_code == 200
    assert attachment_response.status_code == 200
    assert {item["consumer_type"] for item in miniprogram_response.json()["usage"]} == {"group_ops_draft"}
    assert {item["consumer_type"] for item in attachment_response.json()["usage"]} == {"group_ops_draft"}


def test_material_asset_usage_lineage_paginates(client) -> None:
    response = client.get("/api/admin/material-assets/image:12/usage?limit=1")

    assert response.status_code == 200
    body = response.json()
    assert len(body["usage"]) == 1
    assert body["has_more"] is True
    assert body["limit"] == 1
    assert body["offset"] == 0


def test_material_asset_usage_rejects_invalid_asset_id(client) -> None:
    response = client.get("/api/admin/material-assets/unknown:12/usage")

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "material_asset_id" in response.json()["error"]


def test_material_assets_validate_accepts_complete_send_content_package(client) -> None:
    response = client.post(
        "/api/admin/material-assets/validate",
        json={
            "content_package": {
                "image_library_ids": [12],
                "miniprogram_library_ids": [34],
                "attachment_library_ids": [56],
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["read_model"] == "material_asset_validation"
    assert body["valid"] is True
    assert body["issues"] == []
    assert {item["material_asset_id"] for item in body["materials"]} == {"image:12", "miniprogram:34", "attachment:56"}
    assert all("data_base64" not in str(item) for item in body["materials"])


def test_material_assets_validate_reports_missing_material(client) -> None:
    response = client.post(
        "/api/admin/material-assets/validate",
        json={"content_package": {"image_library_ids": [999]}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["issues"][0]["code"] == "material_missing"
    assert body["issues"][0]["material_asset_id"] == "image:999"


def test_material_assets_validate_checks_channel_compatibility(client) -> None:
    response = client.post(
        "/api/admin/material-assets/validate",
        json={
            "channel": "wechat_pay_product_page",
            "content_package": {"attachment_library_ids": [56]},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert {issue["code"] for issue in body["issues"]} == {"material_channel_incompatible"}


def test_material_assets_validate_detects_incomplete_metadata_and_payload_leak(client, monkeypatch) -> None:
    import aicrm_next.engagement.send_content.application as app_module

    monkeypatch.setattr(app_module, "build_send_content_repository", lambda: _InvalidValidationRepository())
    response = client.post(
        "/api/admin/material-assets/validate",
        json={
            "content_package": {
                "image_library_ids": [12],
                "miniprogram_library_ids": [34],
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert {"material_payload_leak", "material_metadata_incomplete"} <= {issue["code"] for issue in body["issues"]}


def test_material_assets_validate_rejects_unknown_channel(client) -> None:
    response = client.post(
        "/api/admin/material-assets/validate",
        json={"channel": "unknown", "content_package": {"image_library_ids": [12]}},
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False


class _LargeMaterialAssetsRepository:
    source_status = "test_large_material_assets"

    def __init__(self) -> None:
        self._data = {
            "image": [_picker_item("image", item_id) for item_id in range(1, 102)],
            "miniprogram": [_picker_item("miniprogram", 201)],
            "attachment": [_picker_item("attachment", 301)],
            "group_invite": [],
        }

    def list_materials(
        self,
        material_type: str,
        *,
        q: str = "",
        enabled_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        del q, enabled_only
        rows = list(self._data[material_type])
        return {"items": rows[offset : offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    def get_materials_by_ids(self, material_type: str, ids: list[int]) -> list[dict]:
        by_id = {int(item["library_id"]): item for item in self._data[material_type]}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def list_material_asset_usage(self, material_type: str, source_id: int, *, limit: int = 100, offset: int = 0) -> dict:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}


class _InvalidValidationRepository:
    source_status = "test_invalid_validation"

    def list_materials(self, material_type: str, *, q: str = "", enabled_only: bool = True, limit: int = 50, offset: int = 0) -> dict:
        del material_type, q, enabled_only, limit, offset
        return {"items": [], "total": 0, "limit": 0, "offset": 0}

    def get_materials_by_ids(self, material_type: str, ids: list[int]) -> list[dict]:
        rows = {
            "image": [
                {
                    **_picker_item("image", 12),
                    "data_base64": "should-not-leak",
                    "metadata": {"mime_type": "image/png"},
                }
            ],
            "miniprogram": [
                {
                    **_picker_item("miniprogram", 34),
                    "thumbnail_url": "",
                    "metadata": {},
                }
            ],
            "attachment": [],
            "group_invite": [],
        }[material_type]
        by_id = {int(item["library_id"]): item for item in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def list_material_asset_usage(self, material_type: str, source_id: int, *, limit: int = 100, offset: int = 0) -> dict:
        del material_type, source_id
        return {"items": [], "total": 0, "limit": limit, "offset": offset}


def _picker_item(material_type: str, item_id: int) -> dict:
    return {
        "type": material_type,
        "library_id": item_id,
        "title": f"{material_type}-{item_id}",
        "subtitle": "",
        "thumbnail_url": "",
        "enabled": True,
        "metadata": {},
    }
