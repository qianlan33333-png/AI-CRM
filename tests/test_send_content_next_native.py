from __future__ import annotations

import pytest

from aicrm_next.automation.automation_engine.repo import reset_automation_fixture_state


@pytest.fixture()
def client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from aicrm_next.main import create_app

    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    reset_automation_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def test_send_content_validate_normalizes_ids_and_agent_text(client) -> None:
    response = client.post(
        "/api/admin/send-content/validate",
        json={
            "content_package": {
                "content_text": "  必须被忽略  ",
                "image_library_ids": [12, 12, 13],
                "miniprogram_library_ids": [34],
                "attachment_library_ids": [56, 56],
                "group_invite_library_ids": [78, 78],
            },
            "text_enabled": False,
            "require_body": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["content_package"] == {
        "content_text": "",
        "image_library_ids": [12, 13],
        "miniprogram_library_ids": [34],
        "attachment_library_ids": [56],
        "group_invite_library_ids": [78],
    }


def test_send_content_validate_rejects_empty_required_body(client) -> None:
    response = client.post(
        "/api/admin/send-content/validate",
        json={"content_package": {}, "require_body": True},
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "不能为空" in response.json()["error"]


def test_send_content_validate_rejects_non_positive_or_boolean_ids(client) -> None:
    response = client.post(
        "/api/admin/send-content/validate",
        json={"content_package": {"image_library_ids": [True]}, "require_body": False},
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "正整数" in response.json()["error"]


def test_send_content_preview_and_material_picker_are_local_only(client) -> None:
    preview_response = client.post(
        "/api/admin/send-content/preview",
        json={
            "content_package": {
                "content_text": "  你好  ",
                "image_library_ids": [12],
                "miniprogram_library_ids": [34],
                "attachment_library_ids": [56],
                "group_invite_library_ids": [78],
            }
        },
    )

    assert preview_response.status_code == 200
    preview = preview_response.json()["preview"]
    assert preview["content_text"] == "你好"
    assert preview["material_summary"] == {
        "image_count": 1,
        "miniprogram_count": 1,
        "attachment_count": 1,
        "group_invite_count": 1,
    }
    assert {item["type"] for item in preview["materials"]} == {"image", "miniprogram", "attachment", "group_invite"}
    invite = next(item for item in preview["materials"] if item["type"] == "group_invite")
    assert invite["metadata"]["join_url"].startswith("https://work.weixin.qq.com/gm/")

    picker_response = client.get("/api/admin/material-picker/items?type=image&limit=500")
    assert picker_response.status_code == 200
    picker = picker_response.json()
    assert picker["limit"] == 100
    assert picker["items"][0]["thumbnail_url"].startswith("/api/admin/image-library/")


def test_material_picker_rejects_unknown_type_with_json(client) -> None:
    response = client.get("/api/admin/material-picker/items?type=video")

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "素材类型必须是 image、miniprogram、attachment 或 group_invite"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/admin/automation-conversion/tasks/1"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-strategy"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-content/unified"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-content/profile-segments/early_founder"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-content/behavior-segments/between_2_9"),
        ("put", "/api/admin/automation-conversion/tasks/1/send-content/agent-materials"),
        ("get", "/api/admin/automation-conversion/behavior-segment-rules"),
    ],
)
def test_legacy_automation_task_content_routes_are_removed(client, method: str, path: str) -> None:
    if method == "get":
        response = client.get(path)
    else:
        response = getattr(client, method)(path, json={"content_package": {"content_text": "旧配置"}})

    assert response.status_code == 404
