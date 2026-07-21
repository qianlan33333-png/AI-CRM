from __future__ import annotations

from typing import Any

import pytest
from tests.admin_auth_test_helpers import access_token_headers, install_access_token

pytest_plugins = ("tests.group_ops_test_helpers",)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"synthetic-png-payload"


@pytest.fixture(autouse=True)
def _broadcast_runtime(monkeypatch):
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.setenv("AICRM_GROUP_OPS_BROADCAST_PLAN_ID", "2")
    monkeypatch.setenv("AICRM_GROUP_OPS_MINIPROGRAM_APPID", "wx-fixture-miniprogram")
    monkeypatch.setenv("AICRM_GROUP_OPS_OUTBOUND_MODE", "external_effect")
    monkeypatch.setenv("AICRM_WECOM_GROUP_ADAPTER_MODE", "fake")
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_WECOM_ENABLED_EFFECT_TYPES", "wecom.message.group.send")


def _machine_headers(client, *, idempotency_key: str = "pytest-group-broadcast-001") -> dict[str, str]:
    token = getattr(client.app.state, "test_group_broadcast_access_token", "")
    if not token:
        token = install_access_token(
            client,
            audience="external_integration",
            capabilities=("group_broadcast_execute",),
            scopes=("write",),
            client_id="pytest-group-broadcast",
            purpose="group_broadcast",
        )
        client.app.state.test_group_broadcast_access_token = token
    headers = access_token_headers(token)
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _post_json(client, payload: dict[str, Any], *, idempotency_key: str = "pytest-group-broadcast-001"):
    return client.post(
        "/api/automation/group-ops/broadcast",
        headers=_machine_headers(client, idempotency_key=idempotency_key),
        json=payload,
    )


def test_group_ops_broadcast_requires_internal_bearer_token(group_ops_api_client, monkeypatch):
    del monkeypatch
    _machine_headers(group_ops_api_client)
    missing = group_ops_api_client.post(
        "/api/automation/group-ops/broadcast",
        headers={"Idempotency-Key": "missing-token"},
        json={"text": "hello"},
    )
    invalid = group_ops_api_client.post(
        "/api/automation/group-ops/broadcast",
        headers={"Authorization": "Bearer wrong", "Idempotency-Key": "wrong-token"},
        json={"text": "hello"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.json()["error"] == "access_token_required"
    assert invalid.json()["error"] == "invalid_access_token"

    machine_authorized = _post_json(
        group_ops_api_client,
        {"text": "machine auth without admin session"},
        idempotency_key="machine-auth-no-admin-session",
    )
    assert machine_authorized.status_code == 202
    assert machine_authorized.json()["status"] == "ready"
    assert machine_authorized.json()["real_external_call_executed"] is False


def test_group_ops_broadcast_requires_idempotency_and_content(group_ops_api_client):
    no_idempotency = group_ops_api_client.post(
        "/api/automation/group-ops/broadcast",
        headers=_machine_headers(group_ops_api_client, idempotency_key=""),
        json={"text": "hello"},
    )
    empty = _post_json(group_ops_api_client, {}, idempotency_key="empty-content")

    assert no_idempotency.status_code == 400
    assert no_idempotency.json()["error"] == "idempotency_key_required"
    assert empty.status_code == 400
    assert empty.json()["error"] == "broadcast_content_required"


def test_group_ops_broadcast_sends_text_only(group_ops_api_client):
    response = _post_json(group_ops_api_client, {"recommendation_text": "text-only group broadcast"})

    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["external_effect_job_id"] > 0
    assert body["execution_id"].startswith("exe_group_ops_")
    assert body["status_url"] == f"/api/admin/executions/{body['execution_id']}"
    assert body["job_ids"] == [body["external_effect_job_id"]]
    assert body["upload_effect_job_ids"] == []
    assert body["content"]["text_present"] is True
    assert body["content"]["uploaded_image_count"] == 0
    assert body["content"]["card_attached"] is False
    assert body["route_owner"] == "ai_crm_next"


def test_group_ops_broadcast_accepts_multipart_images(group_ops_api_client):
    response = group_ops_api_client.post(
        "/api/automation/group-ops/broadcast",
        headers=_machine_headers(group_ops_api_client),
        files=[("images", ("cover.png", PNG_BYTES, "image/png"))],
    )

    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "waiting_dependencies"
    assert len(body["upload_effect_job_ids"]) == 1
    assert body["job_ids"] == [*body["upload_effect_job_ids"], body["final_effect_job_id"]]
    assert body["content"]["text_present"] is False
    assert body["content"]["uploaded_image_count"] == 1
    assert body["content"]["image_count"] == 1
    assert body["content"]["card_attached"] is False
    assert "fake_media" not in response.text


def test_group_ops_broadcast_accepts_card_and_combined_content(group_ops_api_client, monkeypatch):
    from aicrm_next.automation_engine.group_ops import broadcast
    from aicrm_next.integration_gateway.lesson_card_cover_client import LessonCardCover

    calls: list[str] = []

    class FakeCoverClient:
        def download(self, lesson_id: str) -> LessonCardCover:
            calls.append(lesson_id)
            return LessonCardCover(
                file_name=f"lesson-{lesson_id}.png",
                content_type="image/png",
                file_bytes=PNG_BYTES,
            )

    monkeypatch.setattr(broadcast, "build_lesson_card_cover_client", lambda: FakeCoverClient())
    response = group_ops_api_client.post(
        "/api/automation/group-ops/broadcast",
        headers=_machine_headers(group_ops_api_client, idempotency_key="combined-card-image"),
        data={
            "text": "今日日课——《测试卡片标题》",
            "card_path": "pages/article/article?lesson_id=2fe19357-3b07-4547-9a3f-c14696cc81f5&from=learn",
        },
        files=[("images", ("detail.png", PNG_BYTES, "image/png"))],
    )

    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True
    assert body["content"]["card_attached"] is True
    assert body["content"]["card_title"] == "测试卡片标题"
    assert body["content"]["uploaded_image_count"] == 1
    card_only = _post_json(
        group_ops_api_client,
        {"card_path": "pages/article/article?lesson_id=2fe19357-3b07-4547-9a3f-c14696cc81f5&from=learn"},
        idempotency_key="card-only",
    )
    assert card_only.status_code == 202
    assert card_only.json()["content"]["text_present"] is False
    assert card_only.json()["content"]["card_attached"] is True
    assert card_only.json()["content"]["card_title"] == "黄小璨 AI 日课"
    assert calls == [
        "2fe19357-3b07-4547-9a3f-c14696cc81f5",
        "2fe19357-3b07-4547-9a3f-c14696cc81f5",
    ]


def test_group_ops_broadcast_rejects_invalid_card_path_and_image(group_ops_api_client):
    invalid_card = _post_json(
        group_ops_api_client,
        {"card_path": "https://attacker.example/cover.png"},
        idempotency_key="invalid-card",
    )
    invalid_image = group_ops_api_client.post(
        "/api/automation/group-ops/broadcast",
        headers=_machine_headers(group_ops_api_client, idempotency_key="invalid-image"),
        files=[("images", ("fake.png", b"not-an-image", "image/png"))],
    )

    assert invalid_card.status_code == 400
    assert invalid_card.json()["error"] == "invalid_card_path"
    assert invalid_image.status_code == 400
    assert invalid_image.json()["error"] == "invalid_image_content"


def test_group_ops_broadcast_rejects_invalid_existing_media_id(group_ops_api_client):
    response = _post_json(
        group_ops_api_client,
        {"image_media_ids": ["invalid media id"]},
        idempotency_key="invalid-media-id",
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_image_media_id"


def test_group_ops_broadcast_rejects_more_than_three_images(group_ops_api_client):
    response = group_ops_api_client.post(
        "/api/automation/group-ops/broadcast",
        headers=_machine_headers(group_ops_api_client),
        files=[("images", (f"image-{index}.png", PNG_BYTES, "image/png")) for index in range(4)],
    )

    assert response.status_code == 400
    assert response.json()["error"] == "too_many_images"


def test_group_ops_broadcast_idempotency_does_not_send_twice(group_ops_api_client):
    first = _post_json(group_ops_api_client, {"text": "idempotent broadcast"}, idempotency_key="same-request")
    duplicate = _post_json(group_ops_api_client, {"text": "changed text"}, idempotency_key="same-request")

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert first.json()["external_effect_job_id"] == duplicate.json()["external_effect_job_id"]
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["status"] == "ready"
    assert first.json()["execution_id"] == duplicate.json()["execution_id"]
