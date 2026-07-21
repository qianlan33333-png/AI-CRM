from __future__ import annotations

import pytest

from aicrm_next.integration_gateway.wecom_media_upload_client import (
    WeComMediaUploadClient,
    WeComMediaUploadClientError,
)


def test_wecom_media_client_gettoken_and_upload_image() -> None:
    calls: dict[str, list] = {"get": [], "post": []}

    def fake_get(url, *, params, timeout):
        calls["get"].append({"url": url, "params": params, "timeout": timeout})
        return {"errcode": 0, "access_token": "token_001", "expires_in": 7200}

    def fake_post(url, *, params, files, timeout):
        calls["post"].append({"url": url, "params": params, "files": files, "timeout": timeout})
        return {"errcode": 0, "errmsg": "ok", "media_id": "media_001"}

    client = WeComMediaUploadClient(
        corp_id="corp_001",
        secret="secret_001",
        api_base="https://qyapi.example.test",
        timeout=9,
        http_get=fake_get,
        http_post=fake_post,
    )

    first = client.upload_image("probe.png", b"png-bytes", "image/png")
    second = client.upload_image("probe.png", b"png-bytes", "image/png")

    assert first["media_id"] == "media_001"
    assert second["media_id"] == "media_001"
    assert len(calls["get"]) == 1
    assert calls["get"][0]["url"] == "https://qyapi.example.test/cgi-bin/gettoken"
    assert calls["get"][0]["params"] == {"corpid": "corp_001", "corpsecret": "secret_001"}
    assert calls["post"][0]["url"] == "https://qyapi.example.test/cgi-bin/media/upload"
    assert calls["post"][0]["params"] == {"access_token": "token_001", "type": "image"}
    assert calls["post"][0]["files"]["media"] == ("probe.png", b"png-bytes", "image/png")
    assert calls["post"][0]["timeout"] == 9


def test_wecom_media_client_upload_attachment_path_and_params() -> None:
    calls: dict[str, list] = {"get": [], "post": []}

    def fake_get(url, *, params, timeout):
        calls["get"].append({"url": url, "params": params, "timeout": timeout})
        return {"errcode": 0, "access_token": "token_001", "expires_in": 7200}

    def fake_post(url, *, params, files, timeout):
        calls["post"].append({"url": url, "params": params, "files": files, "timeout": timeout})
        return {"errcode": 0, "errmsg": "ok", "media_id": "media_file_001"}

    client = WeComMediaUploadClient(
        corp_id="corp_001",
        secret="secret_001",
        api_base="https://qyapi.example.test",
        timeout=9,
        http_get=fake_get,
        http_post=fake_post,
    )

    result = client.upload_attachment("guide.pdf", b"pdf-bytes", "application/pdf")

    assert result["media_id"] == "media_file_001"
    assert calls["post"][0]["url"] == "https://qyapi.example.test/cgi-bin/media/upload"
    assert calls["post"][0]["params"] == {"access_token": "token_001", "type": "file"}
    assert calls["post"][0]["files"]["media"] == ("guide.pdf", b"pdf-bytes", "application/pdf")


def test_wecom_media_client_missing_config_fails_without_http_call(monkeypatch) -> None:
    for name in (
        "AICRM_WECOM_MEDIA_CORP_ID",
        "AICRM_WECOM_GROUP_CORP_ID",
        "WECOM_CORP_ID",
        "AICRM_WECOM_MEDIA_SECRET",
        "AICRM_WECOM_GROUP_SECRET",
        "WECOM_SECRET",
        "WECOM_CONTACT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    called = {"get": False, "post": False}

    def fake_get(*args, **kwargs):
        called["get"] = True
        return {}

    def fake_post(*args, **kwargs):
        called["post"] = True
        return {}

    client = WeComMediaUploadClient(corp_id="", secret="", http_get=fake_get, http_post=fake_post)

    with pytest.raises(WeComMediaUploadClientError) as exc_info:
        client.upload_image("probe.png", b"x", "image/png")

    assert exc_info.value.error_code == "wecom_media_config_missing"
    assert called == {"get": False, "post": False}


def test_wecom_media_client_gettoken_nonzero_fails() -> None:
    client = WeComMediaUploadClient(
        corp_id="corp_001",
        secret="secret_001",
        http_get=lambda *args, **kwargs: {"errcode": 40013, "errmsg": "invalid corpid"},
        http_post=lambda *args, **kwargs: {"errcode": 0, "media_id": "unused"},
    )

    with pytest.raises(WeComMediaUploadClientError) as exc_info:
        client.get_access_token()

    assert exc_info.value.error_code == "wecom_media_gettoken_failed"
    assert exc_info.value.payload["errcode"] == 40013


def test_wecom_media_client_upload_nonzero_fails() -> None:
    client = WeComMediaUploadClient(
        corp_id="corp_001",
        secret="secret_001",
        http_get=lambda *args, **kwargs: {"errcode": 0, "access_token": "token_001", "expires_in": 7200},
        http_post=lambda *args, **kwargs: {"errcode": 40007, "errmsg": "invalid media"},
    )

    with pytest.raises(WeComMediaUploadClientError) as exc_info:
        client.upload_image("probe.png", b"x", "image/png")

    assert exc_info.value.error_code == "wecom_media_upload_failed"
    assert exc_info.value.payload["errcode"] == 40007
    assert exc_info.value.provider_errcode == 40007
    assert exc_info.value.provider_result_received is True
    assert exc_info.value.errmsg_present is True


def test_wecom_media_client_malformed_errcode_does_not_raise_conversion_error() -> None:
    client = WeComMediaUploadClient(
        corp_id="corp_001",
        secret="secret_001",
        http_get=lambda *args, **kwargs: {"errcode": 0, "access_token": "token_001", "expires_in": 7200},
        http_post=lambda *args, **kwargs: {"errcode": "malformed", "errmsg": "invalid response"},
    )

    with pytest.raises(WeComMediaUploadClientError) as exc_info:
        client.upload_image("probe.png", b"x", "image/png")

    assert exc_info.value.error_code == "wecom_media_upload_failed"
    assert exc_info.value.provider_errcode == 0
    assert exc_info.value.provider_result_received is True


def test_wecom_media_client_http_exception_fails() -> None:
    def fake_get(*args, **kwargs):
        raise RuntimeError("network down")

    client = WeComMediaUploadClient(
        corp_id="corp_001",
        secret="secret_001",
        http_get=fake_get,
        http_post=lambda *args, **kwargs: {"errcode": 0, "media_id": "unused"},
    )

    with pytest.raises(WeComMediaUploadClientError) as exc_info:
        client.get_access_token()

    assert exc_info.value.error_code == "wecom_media_http_error"
