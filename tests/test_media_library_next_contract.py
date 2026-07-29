from __future__ import annotations

import base64
import sys
import types
from io import BytesIO

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdac`\x00\x01\x00\x00\x07\x00\x01\xe9\x15\x08-"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def make_client() -> TestClient:
    return TestClient(create_app())


def assert_json_contract(payload: dict, *, ok: bool = True) -> None:
    assert payload["ok"] is ok
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["storage_adapter_mode"]
    assert payload["adapter_mode"]
    assert payload["source_status"]


def test_media_library_list_and_page_routes_have_next_contract() -> None:
    client = make_client()

    for path in ["/admin/image-library", "/admin/attachment-library", "/admin/miniprogram-library"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"

    for path in ["/api/admin/image-library", "/api/admin/attachment-library", "/api/admin/miniprogram-library"]:
        payload = client.get(path).json()
        assert_json_contract(payload)
        assert isinstance(payload["items"], list)
        assert payload["count"] == len(payload["items"])
        assert "total" in payload


def test_media_library_production_mode_env_is_not_exposed_as_available(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_MEDIA_STORAGE_MODE", "production")
    monkeypatch.setenv("AICRM_NEXT_WECOM_MEDIA_MODE", "production")
    client = make_client()

    payload = client.get("/api/admin/image-library").json()

    assert_json_contract(payload)
    assert payload["adapter_mode"] == "fake"
    assert payload["storage_adapter_mode"] == "fake"


def test_media_library_postgres_connection_does_not_reuse_sqlalchemy_raw_pool(monkeypatch) -> None:
    from aicrm_next.engagement.media_library.repo import connect_media_library_db

    calls: list[dict[str, object]] = []
    dict_row = object()
    fake_psycopg = types.ModuleType("psycopg")
    fake_rows = types.ModuleType("psycopg.rows")
    fake_rows.dict_row = dict_row

    def fake_connect(url: str, *, row_factory: object | None = None) -> str:
        calls.append({"url": url, "row_factory": row_factory})
        return "raw-psycopg-connection"

    fake_psycopg.connect = fake_connect
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_rows)

    result = connect_media_library_db("postgresql+psycopg://user:pass@localhost/db")

    assert result == "raw-psycopg-connection"
    assert calls == [{"url": "postgresql://user:pass@localhost/db", "row_factory": dict_row}]


def test_upload_update_delete_routes_report_local_side_effect_plan() -> None:
    client = make_client()

    image = client.post(
        "/api/admin/image-library/upload",
        files={"image": ("contract.png", BytesIO(TINY_PNG), "image/png")},
        data={"name": "contract image"},
        headers={"Idempotency-Key": "contract-upload-1"},
    ).json()
    assert_json_contract(image)
    assert image["source_status"] == "local_upload"
    assert image["side_effect_plan"]["external_storage"] == "not_executed"
    assert image["side_effect_plan"]["wecom_media_upload"] == "not_executed"
    assert image["side_effect_plan"]["idempotency_key"] == "contract-upload-1"

    image_id = image["item"]["id"]
    updated = client.put(f"/api/admin/image-library/{image_id}", json={"name": "renamed"}).json()
    assert_json_contract(updated)
    assert updated["item"]["name"] == "renamed"

    deleted = client.delete(f"/api/admin/image-library/{image_id}").json()
    assert_json_contract(deleted)
    assert deleted["deleted"] is True
    assert deleted["side_effect_plan"]["real_external_call"] == "not_executed"

    attachment = client.post(
        "/api/admin/attachment-library/upload",
        files={"attachment": ("guide.pdf", BytesIO(PDF_BYTES), "application/pdf")},
        data={"name": "contract pdf"},
    ).json()
    assert_json_contract(attachment)
    assert attachment["side_effect_plan"]["external_storage"] == "not_executed"

    mini = client.post(
        "/api/admin/miniprogram-library",
        json={
            "name": "contract mini",
            "appid": "wx-contract",
            "pagepath": "pages/contract/index",
            "title": "contract card",
            "thumb_image_id": "image_masked_001",
            "resolve_thumb_media": False,
        },
    ).json()
    assert_json_contract(mini)
    mini_id = mini["item"]["id"]
    mini_detail = client.get(f"/api/admin/miniprogram-library/{mini_id}").json()
    assert_json_contract(mini_detail)
    mini_deleted = client.delete(f"/api/admin/miniprogram-library/{mini_id}").json()
    assert_json_contract(mini_deleted)


def test_miniprogram_create_accepts_app_id_and_preserves_thumb_media_id() -> None:
    client = make_client()

    mini = client.post(
        "/api/admin/miniprogram-library",
        json={
            "name": "contract mini alias",
            "app_id": "wx-contract-alias",
            "page_path": "pages/contract/alias",
            "title": "contract alias card",
            "thumb_media_id": "test_thumb_media_id",
            "resolve_thumb_media": False,
        },
    ).json()

    assert_json_contract(mini)
    assert mini["item"]["appid"] == "wx-contract-alias"
    assert mini["item"]["page_path"] == "pages/contract/alias"
    assert mini["item"]["thumb_media_id"] == "test_thumb_media_id"
    assert mini["item"].get("thumb_image_id") in (None, "")


def test_import_routes_accept_idempotency_and_do_not_execute_real_external_calls() -> None:
    client = make_client()

    from_url = client.post(
        "/api/admin/image-library/from-url",
        json={"url": "https://example.invalid/image.png", "name": "remote reference"},
        headers={"Idempotency-Key": "contract-url-1"},
    ).json()
    assert_json_contract(from_url)
    assert from_url["source_status"] == "fake_import"
    assert from_url["adapter_result"]["cloud_storage"]["idempotency_key"] == "contract-url-1:cloud"
    assert from_url["adapter_result"]["wecom_media"]["idempotency_key"] == "contract-url-1:wecom"

    from_base64 = client.post(
        "/api/admin/image-library/from-base64",
        json={
            "data_base64": base64.b64encode(TINY_PNG).decode("ascii"),
            "file_name": "contract-base64.png",
            "name": "base64 reference",
        },
        headers={"Idempotency-Key": "contract-base64-1"},
    ).json()
    assert_json_contract(from_base64)
    assert from_base64["source_status"] == "fake_import"
    assert from_base64["adapter_result"]["cloud_storage"]["side_effect_executed"] is False
    assert from_base64["adapter_result"]["wecom_media"]["side_effect_executed"] is False

    from_data_url = client.post(
        "/api/admin/image-library/from-base64",
        json={
            "data_url": "data:image/png;base64," + base64.b64encode(TINY_PNG).decode("ascii"),
            "file_name": "contract-data-url.png",
            "name": "data url reference",
        },
        headers={"Idempotency-Key": "contract-data-url-1"},
    ).json()
    assert_json_contract(from_data_url)
    assert from_data_url["source_status"] == "fake_import"
    assert from_data_url["adapter_result"]["cloud_storage"]["idempotency_key"] == "contract-data-url-1:cloud"


def test_thumbnail_and_variant_routes_are_binary_next_contract_surfaces() -> None:
    client = make_client()
    created = client.post(
        "/api/admin/image-library/upload",
        files={"image": ("binary.png", BytesIO(TINY_PNG), "image/png")},
    ).json()["item"]

    thumbnail = client.get(f"/api/admin/image-library/{created['id']}/thumbnail?size=160")
    assert thumbnail.status_code == 200
    assert thumbnail.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert thumbnail.headers["X-AICRM-Fallback-Used"] == "false"
    assert thumbnail.headers["X-AICRM-Real-External-Call-Executed"] == "false"
    assert thumbnail.headers["X-AICRM-Storage-Adapter-Mode"]

    variant = client.get(f"/api/admin/image-library/{created['id']}/variants/thumb_160")
    assert variant.status_code == 200
    assert variant.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert variant.headers["X-AICRM-Fallback-Used"] == "false"
    assert variant.headers["X-AICRM-Real-External-Call-Executed"] == "false"

    missing = client.get("/api/admin/image-library/missing/thumbnail?size=160")
    assert missing.status_code == 404
    assert_json_contract(missing.json(), ok=False)
