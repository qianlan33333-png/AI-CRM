from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.automation.automation_engine import channels_api
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "qrcode-download-consistency")
    channels_api._FIXTURE_CHANNELS.clear()
    channels_api._NEXT_ID = 1
    return TestClient(create_app(), raise_server_exceptions=False)


def test_un_generated_qrcode_channel_cannot_download_old_url(monkeypatch):
    client = _client(monkeypatch)
    channel = client.post("/api/admin/channels", json={"channel_name": "未生成二维码", "channel_code": "fake_scene"}).json()["channel"]

    response = client.get(f"/api/admin/channels/{channel['id']}/qrcode/download", follow_redirects=False)

    assert response.status_code == 409
    assert response.json()["reason"] == "qrcode_not_generated"


def test_download_redirects_only_current_active_asset(monkeypatch):
    client = _client(monkeypatch)
    channel = client.post("/api/admin/channels", json={"channel_name": "已生成二维码", "channel_code": "signup"}).json()["channel"]
    channel_id = int(channel["id"])
    channels_api._FIXTURE_CHANNELS[channel_id]["scene_value"] = "aqr_current"
    channels_api._FIXTURE_CHANNELS[channel_id]["qr_url"] = "https://wework.qpic.cn/current"
    channels_api._FIXTURE_CHANNELS[channel_id]["_active_qrcode_asset"] = {
        "id": 9,
        "channel_id": channel_id,
        "scene_value": "aqr_current",
        "qr_url": "https://wework.qpic.cn/current",
        "status": "active",
    }

    response = client.get(f"/api/admin/channels/{channel_id}/qrcode/download", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://wework.qpic.cn/current"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-aicrm-channel-id"] == str(channel_id)
    assert response.headers["x-aicrm-qr-scene"] == "aqr_current"
    assert response.headers["x-aicrm-qr-asset-id"] == "9"


def test_download_rejects_channel_cache_and_asset_mismatch(monkeypatch):
    client = _client(monkeypatch)
    channel = client.post("/api/admin/channels", json={"channel_name": "错配二维码", "channel_code": "signup"}).json()["channel"]
    channel_id = int(channel["id"])
    channels_api._FIXTURE_CHANNELS[channel_id]["scene_value"] = "program_3_default_qrcode"
    channels_api._FIXTURE_CHANNELS[channel_id]["qr_url"] = "https://wework.qpic.cn/stale"
    channels_api._FIXTURE_CHANNELS[channel_id]["_active_qrcode_asset"] = {
        "id": 10,
        "channel_id": channel_id,
        "scene_value": "aqr_actual",
        "qr_url": "https://wework.qpic.cn/actual",
        "status": "active",
    }

    response = client.get(f"/api/admin/channels/{channel_id}/qrcode/download", follow_redirects=False)

    assert response.status_code == 409
    assert response.json()["reason"] == "qrcode_asset_mismatch"
