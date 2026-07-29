from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from aicrm_next.automation.automation_engine import channels_api
from aicrm_next.channels.integration_gateway.wecom_qrcode_image_client import (
    WeComQrImage,
    WeComQrImageClientError,
)
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


def _png_bytes() -> bytes:
    output = BytesIO()
    image = Image.new("RGBA", (420, 420), (0, 0, 0, 0))
    image.putpixel((210, 210), (0, 0, 0, 255))
    image.save(output, "PNG")
    return output.getvalue()


class _FakeQrClient:
    def __init__(self, result=None, error=None):
        self.result = result or WeComQrImage(file_bytes=_png_bytes(), content_type="image/png")
        self.error = error
        self.urls = []

    def download(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.result


def test_download_returns_current_active_asset_as_jpeg_attachment(monkeypatch):
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

    qr_client = _FakeQrClient()
    monkeypatch.setattr(channels_api, "build_wecom_qrcode_image_client", lambda: qr_client)
    response = client.get(f"/api/admin/channels/{channel_id}/qrcode/download", follow_redirects=False)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert "attachment" in response.headers["content-disposition"]
    assert "channel-" in response.headers["content-disposition"]
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert "location" not in response.headers
    assert response.content.startswith(b"\xff\xd8")
    assert response.content.endswith(b"\xff\xd9")
    with Image.open(BytesIO(response.content)) as downloaded:
        assert downloaded.mode == "RGB"
        assert downloaded.size == (420, 420)
        assert downloaded.getpixel((0, 0)) == (255, 255, 255)
    assert qr_client.urls == ["https://wework.qpic.cn/current"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-aicrm-channel-id"] == str(channel_id)
    assert response.headers["x-aicrm-qr-scene"] == "aqr_current"
    assert response.headers["x-aicrm-qr-asset-id"] == "9"


def test_download_rejects_non_trusted_asset_url_as_explicit_502(monkeypatch):
    client = _client(monkeypatch)
    channel = client.post("/api/admin/channels", json={"channel_name": "非法域名", "channel_code": "signup"}).json()["channel"]
    channel_id = int(channel["id"])
    channels_api._FIXTURE_CHANNELS[channel_id]["scene_value"] = "aqr_untrusted"
    channels_api._FIXTURE_CHANNELS[channel_id]["qr_url"] = "https://evil.example/qr.png"
    channels_api._FIXTURE_CHANNELS[channel_id]["_active_qrcode_asset"] = {
        "id": 11,
        "channel_id": channel_id,
        "scene_value": "aqr_untrusted",
        "qr_url": "https://evil.example/qr.png",
        "status": "active",
    }

    response = client.get(f"/api/admin/channels/{channel_id}/qrcode/download")

    assert response.status_code == 502
    assert response.json()["reason"] == "qrcode_image_host_not_allowed"


def test_download_maps_upstream_timeout_to_explicit_504(monkeypatch):
    client = _client(monkeypatch)
    channel = client.post("/api/admin/channels", json={"channel_name": "上游超时", "channel_code": "signup"}).json()["channel"]
    channel_id = int(channel["id"])
    channels_api._FIXTURE_CHANNELS[channel_id]["scene_value"] = "aqr_timeout"
    channels_api._FIXTURE_CHANNELS[channel_id]["qr_url"] = "https://wework.qpic.cn/timeout"
    channels_api._FIXTURE_CHANNELS[channel_id]["_active_qrcode_asset"] = {
        "id": 12,
        "channel_id": channel_id,
        "scene_value": "aqr_timeout",
        "qr_url": "https://wework.qpic.cn/timeout",
        "status": "active",
    }
    monkeypatch.setattr(
        channels_api,
        "build_wecom_qrcode_image_client",
        lambda: _FakeQrClient(error=WeComQrImageClientError("qrcode_image_upstream_timeout", timed_out=True)),
    )

    response = client.get(f"/api/admin/channels/{channel_id}/qrcode/download")

    assert response.status_code == 504
    assert response.json()["reason"] == "qrcode_image_upstream_timeout"


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
