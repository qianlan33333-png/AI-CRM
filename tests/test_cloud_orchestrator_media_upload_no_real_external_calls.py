from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLOUD_ROOT = ROOT / "aicrm_next/extensions/growth/cloud_orchestrator"
MEDIA_UPLOAD = CLOUD_ROOT / "media_upload.py"
MEDIA_CLIENT = ROOT / "aicrm_next/integration_gateway/wecom_media_upload_client.py"


def _source() -> str:
    return MEDIA_UPLOAD.read_text(encoding="utf-8")


def test_cloud_orchestrator_media_upload_uses_approved_wecom_gateway_boundary():
    source = _source()
    forbidden = [
        "legacy_wecom_client_from_app",
        "legacy_flask_facade",
        "_legacy" + "_app",
        "_upload" + "_private_message_image",
        "wecom_ability" + "_service",
        "WeComClient" + ".from_app",
    ]
    required = [
        "build_wecom_media_upload_client",
        "WeComMediaUploadClientError",
        "wecom_media_upload_executed",
        "real_external_call_executed",
    ]

    for marker in forbidden:
        assert marker not in source
    for marker in required:
        assert marker in source


def test_cloud_orchestrator_media_upload_does_not_use_direct_http_clients():
    source = _source()

    assert "request" + "s." not in source
    assert "http" + "x" not in source


def test_cloud_orchestrator_media_upload_marks_real_call_only_in_upload_path():
    source = _source()

    assert "real_external_call_executed" in source
    assert "wecom_media_upload_executed" in source
    assert "real_external_call_executed=True" not in source
    assert "wecom_media_upload_executed=True" not in source


def test_wecom_media_upload_client_is_the_only_direct_http_boundary():
    cloud_source = _source()
    client_source = MEDIA_CLIENT.read_text(encoding="utf-8")

    assert "requests." not in cloud_source
    assert "httpx" not in cloud_source
    assert "access" + "_token" not in cloud_source
    assert "/cgi-bin/media/upload" not in cloud_source
    assert "/cgi-bin/gettoken" not in cloud_source

    assert "requests." in client_source
    assert "/cgi-bin/media/upload" in client_source
    assert "/cgi-bin/gettoken" in client_source
    for marker in (
        "legacy_flask_facade",
        "_legacy" + "_app",
        "legacy_wecom_client_from_app",
        "wecom_ability" + "_service",
    ):
        assert marker not in client_source
