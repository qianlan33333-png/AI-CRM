from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.media_library.repo import InMemoryMediaLibraryRepository
from aicrm_next.platform.shared.errors import ContractError


TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _client() -> TestClient:
    return TestClient(create_app())


def _media_library_source() -> str:
    root = Path(__file__).resolve().parents[1] / "aicrm_next" / "media_library"
    return "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))


def test_media_library_source_has_no_direct_external_clients() -> None:
    source = _media_library_source()
    forbidden = [
        "requests" + ".get",
        "requests" + ".post",
        "http" + "x",
        "boto" + "3",
        "upload" + "_media",
        "access" + "_token",
        "real_external_call_executed" + "=True",
        "real_external_call_executed" + " = True",
    ]

    for token in forbidden:
        assert token not in source


def test_thumbnail_remote_source_fallback_is_blocked_without_network_fetch() -> None:
    repo = InMemoryMediaLibraryRepository(
        {
            "image": [
                {
                    "id": "image_remote_only",
                    "name": "remote only",
                    "file_name": "remote.png",
                    "source": "remote",
                    "source_url": "https://example.com/remote.png",
                    "data_base64": "",
                    "mime_type": "image/png",
                    "content_type": "image/png",
                    "file_size": 0,
                    "width": 1,
                    "height": 1,
                    "enabled": True,
                    "description": "",
                    "tags": [],
                    "category": "",
                    "ai_metadata": {},
                    "created_at": "2026-05-20T12:00:00Z",
                    "updated_at": "2026-05-20T12:00:00Z",
                    "deleted": False,
                }
            ],
            "attachment": [],
            "miniprogram": [],
        }
    )

    with pytest.raises(ContractError, match="remote source fetch is disabled"):
        repo.get_image_thumbnail("image_remote_only", 160)


def test_from_url_uses_guarded_fake_import_and_does_not_fetch_remote_url() -> None:
    payload = _client().post(
        "/api/admin/image-library/from-url",
        json={"url": "https://example.com/test.png", "name": "remote reference"},
        headers={"Idempotency-Key": "no-real-url-1"},
    ).json()

    assert payload["ok"] is True
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["adapter_result"]["side_effect_safety"]["remote_url_fetched"] is False
    assert payload["adapter_result"]["side_effect_safety"]["side_effect_executed"] is False
    assert payload["adapter_result"]["cloud_storage"]["side_effect_executed"] is False
    assert payload["adapter_result"]["wecom_media"]["side_effect_executed"] is False


def test_upload_and_base64_import_report_no_real_external_side_effects() -> None:
    client = _client()

    upload = client.post(
        "/api/admin/image-library/upload",
        files={"image": ("tiny.png", base64.b64decode(TINY_PNG_BASE64), "image/png")},
        data={"name": "tiny"},
        headers={"Idempotency-Key": "no-real-upload-1"},
    ).json()
    assert upload["ok"] is True
    assert upload["fallback_used"] is False
    assert upload["real_external_call_executed"] is False
    assert upload["side_effect_plan"]["external_storage"] == "not_executed"
    assert upload["side_effect_plan"]["wecom_media_upload"] == "not_executed"
    assert upload["side_effect_plan"]["idempotency_key"] == "no-real-upload-1"

    imported = client.post(
        "/api/admin/image-library/from-base64",
        json={"data_url": "data:image/png;base64," + TINY_PNG_BASE64, "name": "data-url"},
        headers={"Idempotency-Key": "no-real-data-url-1"},
    ).json()
    assert imported["ok"] is True
    assert imported["fallback_used"] is False
    assert imported["real_external_call_executed"] is False
    assert imported["adapter_result"]["cloud_storage"]["side_effect_executed"] is False
    assert imported["adapter_result"]["wecom_media"]["side_effect_executed"] is False
