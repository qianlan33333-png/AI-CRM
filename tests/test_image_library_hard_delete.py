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
    monkeypatch.setenv("SECRET_KEY", "next-image-hard-delete-test")
    reset_media_library_fixture_state()
    return TestClient(create_app())


def assert_json_contract(payload: dict, *, ok: bool = True) -> None:
    assert payload["ok"] is ok
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False


def test_image_hard_delete_blocks_references_then_force_clears_them(monkeypatch) -> None:
    client = make_client(monkeypatch)

    image = client.post(
        "/api/admin/image-library/upload",
        files={"image": ("referenced.png", BytesIO(TINY_PNG), "image/png")},
    ).json()["item"]
    mini = client.post(
        "/api/admin/miniprogram-library",
        json={
            "name": "referencing mini",
            "appid": "wx-hard-delete",
            "pagepath": "pages/delete/index",
            "title": "Delete Contract",
            "thumb_image_id": image["id"],
            "resolve_thumb_media": False,
        },
    ).json()
    assert_json_contract(mini)

    blocked_response = client.delete(f"/api/admin/image-library/{image['id']}")
    blocked = blocked_response.json()
    assert blocked_response.status_code == 409
    assert_json_contract(blocked, ok=False)
    assert blocked["error"] == "image_has_references"
    assert blocked["references"]["miniprograms"][0]["id"] == mini["item"]["id"]

    forced = client.delete(f"/api/admin/image-library/{image['id']}?force=true").json()
    assert_json_contract(forced)
    assert forced["deleted"] is True
    assert forced["references_cleared"]["miniprograms_cleared"] == 1
    assert forced["side_effect_plan"]["real_external_call"] == "not_executed"

    missing = client.get(f"/api/admin/image-library/{image['id']}")
    assert missing.status_code == 404
