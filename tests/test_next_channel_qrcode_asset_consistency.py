from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.automation.automation_engine import channels_api
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "qrcode-asset-consistency")
    channels_api._FIXTURE_CHANNELS.clear()
    channels_api._NEXT_ID = 1
    return TestClient(create_app(), raise_server_exceptions=False)


def test_new_qrcode_channel_does_not_create_fake_scene_or_url(monkeypatch):
    client = _client(monkeypatch)

    created = client.post("/api/admin/channels", json={"channel_name": "新渠道", "channel_code": "program_9_default_qrcode"})

    assert created.status_code == 201
    channel = created.json()["channel"]
    assert channel["scene_value"] == ""
    assert channel["qr_url"] == ""
    assert channel["qrcode_status"] == "not_generated"


def test_edit_welcome_and_tag_does_not_change_scene_or_url(monkeypatch):
    client = _client(monkeypatch)
    channel = client.post("/api/admin/channels", json={"channel_name": "新渠道", "channel_code": "signup"}).json()["channel"]
    channel_id = int(channel["id"])
    channels_api._FIXTURE_CHANNELS[channel_id]["scene_value"] = "aqr_generated"
    channels_api._FIXTURE_CHANNELS[channel_id]["qr_url"] = "https://wework.qpic.cn/generated"

    updated = client.patch(
        f"/api/admin/channels/{channel_id}",
        json={"welcome_message": "新欢迎语", "entry_tag_id": "tag-a", "entry_tag_name": "标签A"},
    )

    assert updated.status_code == 200
    assert updated.json()["channel"]["scene_value"] == "aqr_generated"
    assert updated.json()["channel"]["qr_url"] == "https://wework.qpic.cn/generated"
    assert channels_api._FIXTURE_CHANNELS[channel_id].get("_scene_aliases") is None


def test_channel_auto_accept_friend_saves_and_reads_back(monkeypatch):
    client = _client(monkeypatch)

    created = client.post(
        "/api/admin/channels",
        json={"channel_name": "自动通过渠道", "channel_code": "auto-pass", "auto_accept_friend": True},
    )

    assert created.status_code == 201
    channel = created.json()["channel"]
    assert channel["auto_accept_friend"] is True

    channel_id = int(channel["id"])
    loaded = client.get(f"/api/admin/channels/{channel_id}")
    assert loaded.status_code == 200
    assert loaded.json()["channel"]["auto_accept_friend"] is True

    disabled = client.patch(f"/api/admin/channels/{channel_id}", json={"auto_accept_friend": "0"})
    assert disabled.status_code == 200
    assert disabled.json()["channel"]["auto_accept_friend"] is False


def test_user_payload_cannot_mutate_system_managed_scene_or_qr_url(monkeypatch):
    client = _client(monkeypatch)
    channel = client.post("/api/admin/channels", json={"channel_name": "新渠道", "channel_code": "signup"}).json()["channel"]
    channel_id = int(channel["id"])

    rejected_scene = client.patch(f"/api/admin/channels/{channel_id}", json={"scene_value": "manual-scene"})
    rejected_url = client.patch(f"/api/admin/channels/{channel_id}", json={"qr_url": "https://wework.qpic.cn/manual"})

    assert rejected_scene.status_code == 400
    assert "scene_value_is_system_managed" in rejected_scene.text
    assert rejected_url.status_code == 400
    assert "qr_url_is_system_managed" in rejected_url.text
