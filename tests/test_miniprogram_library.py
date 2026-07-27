from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.engagement.media_library.repo import reset_media_library_fixture_state


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdac`\x00\x01\x00\x00\x07\x00\x01\xe9\x15\x08-"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "next-miniprogram-library-test")
    reset_media_library_fixture_state()
    return TestClient(create_app())


def assert_json_contract(payload: dict, *, ok: bool = True) -> None:
    assert payload["ok"] is ok
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False


def test_miniprogram_library_crud_uses_next_media_fixture(monkeypatch) -> None:
    client = make_client(monkeypatch)

    created = client.post(
        "/api/admin/miniprogram-library",
        json={
            "name": "trial card",
            "app_id": "wx-card",
            "page_path": "pages/trial/index",
            "title": "Trial Card",
            "thumb_media_id": "test_thumb_media_id",
            "resolve_thumb_media": False,
        },
    ).json()
    assert_json_contract(created)
    assert created["source_status"] == "local_repository_write"
    assert created["item"]["appid"] == "wx-card"
    assert created["item"]["page_path"] == "pages/trial/index"
    assert created["item"]["thumb_media_id"] == "test_thumb_media_id"
    item_id = created["item"]["id"]

    listed = client.get("/api/admin/miniprogram-library?enabled_only=false&q=trial").json()
    assert_json_contract(listed)
    assert item_id in {item["id"] for item in listed["items"]}

    updated = client.put(
        f"/api/admin/miniprogram-library/{item_id}",
        json={"title": "Trial Card Updated", "enabled": False, "resolve_thumb_media": False},
    ).json()
    assert_json_contract(updated)
    assert updated["item"]["title"] == "Trial Card Updated"
    assert updated["item"]["enabled"] is False

    deleted = client.delete(f"/api/admin/miniprogram-library/{item_id}").json()
    assert_json_contract(deleted)
    assert deleted["deleted"] is True


def test_miniprogram_thumb_media_resolve_uses_fake_adapter(monkeypatch) -> None:
    client = make_client(monkeypatch)

    image = client.post(
        "/api/admin/image-library/upload",
        files={"image": ("thumb.png", BytesIO(TINY_PNG), "image/png")},
    ).json()["item"]
    mini = client.post(
        "/api/admin/miniprogram-library",
        json={
            "name": "resolve card",
            "appid": "wx-resolve",
            "pagepath": "pages/resolve/index",
            "title": "Resolve Card",
            "thumb_image_id": image["id"],
            "resolve_thumb_media": False,
        },
    ).json()["item"]

    resolved = client.post(f"/api/admin/miniprogram-library/{mini['id']}/test-resolve").json()
    assert_json_contract(resolved)
    assert resolved["source_status"] == "wecom_media_plan_or_cache"
    assert resolved["adapter_result"]["side_effect_executed"] is False
    assert resolved["item"]["thumb_media_id"].startswith("fake_wecom_media_")


def test_miniprogram_library_rejects_missing_required_fields(monkeypatch) -> None:
    client = make_client(monkeypatch)

    response = client.post(
        "/api/admin/miniprogram-library",
        json={"name": "broken", "appid": "wx-broken", "resolve_thumb_media": False},
    )

    assert response.status_code == 400
    payload = response.json()
    assert_json_contract(payload, ok=False)
    assert "missing" in payload["error"].lower() or "缺少" in payload["error"]
