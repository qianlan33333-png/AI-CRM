from __future__ import annotations

from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, WECOM_MESSAGE_GROUP_SEND
from tests.group_ops_test_helpers import error_code, group_ops_api_client


def _bind_three_groups(client) -> None:
    for chat_id in ("wrOgAAA002", "wrOgAAA003"):
        response = client.post(
            "/api/admin/automation-conversion/group-ops/plans/2/groups",
            json={"chat_id": chat_id, "operator": "pytest"},
        )
        assert response.status_code == 201


def _webhook_payload(idempotency_key: str = "daily-lesson-2026-05-25") -> dict:
    return {
        "idempotency_key": idempotency_key,
        "send_mode": "queued",
        "scheduled_at": "2026-05-25T20:00:00+08:00",
        "content": {
            "text": "今天的日课已经更新，请大家在 21:00 前完成练习。",
            "attachments": [],
        },
    }


def _wecom_group_jobs():
    return ExternalEffectService().list_jobs({"effect_type": WECOM_MESSAGE_GROUP_SEND}, limit=20)[0]


def test_webhook_accepts_and_queues_fixed_group_external_effect(group_ops_api_client, monkeypatch):
    monkeypatch.setenv("AICRM_GROUP_OPS_OUTBOUND_MODE", "shadow")
    _bind_three_groups(group_ops_api_client)

    response = group_ops_api_client.post(
        "/api/automation/group-ops/webhooks/daily-lesson-8f3a",
        headers={"Authorization": "Bearer fixture-webhook-token"},
        json=_webhook_payload(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["event"]["status"] == "queued"
    assert body["event"]["idempotency_key"] == "daily-lesson-2026-05-25"
    assert body["broadcast_job_ids"] == []
    assert body["legacy_broadcast_job_ids"] == []
    assert body["outbound_mode"] == "external_effect"
    jobs = _wecom_group_jobs()
    assert body["external_effect_job_ids"] == [jobs[0].id]
    assert jobs[0].scheduled_at == "2026-05-25T12:00:00Z"
    assert jobs[0].payload_json["owner_userid"] == "owner_001"
    assert jobs[0].payload_json["chat_ids"] == ["wrOgAAA001", "wrOgAAA002", "wrOgAAA003"]
    assert jobs[0].payload_json["content_payload"]["channel"] == "wecom_customer_group"
    assert jobs[0].payload_json["content_payload"]["sender"] == "owner_001"
    assert jobs[0].payload_json["content_payload"]["text"]["content"].startswith("今天的日课")


def test_webhook_idempotency_does_not_enqueue_twice(group_ops_api_client, monkeypatch):
    monkeypatch.setenv("AICRM_GROUP_OPS_OUTBOUND_MODE", "shadow")

    first = group_ops_api_client.post(
        "/api/automation/group-ops/webhooks/daily-lesson-8f3a",
        headers={"Authorization": "Bearer fixture-webhook-token"},
        json=_webhook_payload(),
    )
    duplicate = group_ops_api_client.post(
        "/api/automation/group-ops/webhooks/daily-lesson-8f3a",
        headers={"Authorization": "Bearer fixture-webhook-token"},
        json=_webhook_payload(),
    )

    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert first.json()["broadcast_job_ids"] == []
    assert duplicate.json()["broadcast_job_ids"] == []
    assert len(_wecom_group_jobs()) == 1


def test_webhook_handler_no_longer_parses_legacy_bearer_tokens(group_ops_api_client, monkeypatch):
    monkeypatch.setenv("AICRM_GROUP_OPS_OUTBOUND_MODE", "shadow")

    missing = group_ops_api_client.post(
        "/api/automation/group-ops/webhooks/daily-lesson-8f3a",
        json=_webhook_payload("no-authorization-header"),
    )
    wrong = group_ops_api_client.post(
        "/api/automation/group-ops/webhooks/daily-lesson-8f3a",
        headers={"Authorization": "Bearer wrong"},
        json=_webhook_payload("obsolete-bearer-header"),
    )

    assert missing.status_code == 202
    assert wrong.status_code == 202
    assert missing.json()["status"] == "queued"
    assert wrong.json()["status"] == "queued"
    assert len(_wecom_group_jobs()) == 2


def test_webhook_empty_content_is_rejected_before_queue(group_ops_api_client, monkeypatch):
    response = group_ops_api_client.post(
        "/api/automation/group-ops/webhooks/daily-lesson-8f3a",
        headers={"Authorization": "Bearer fixture-webhook-token"},
        json={
            "idempotency_key": "empty-content",
            "send_mode": "queued",
            "content": {"text": "", "attachments": []},
        },
    )

    assert response.status_code == 400
    assert error_code(response) == "content_required"


def test_webhook_inactive_plan_returns_409_without_queue(group_ops_api_client, monkeypatch):
    disabled = group_ops_api_client.put(
        "/api/admin/automation-conversion/group-ops/plans/2",
        json={"status": "disabled", "operator": "pytest"},
    )
    assert disabled.status_code == 200

    response = group_ops_api_client.post(
        "/api/automation/group-ops/webhooks/daily-lesson-8f3a",
        headers={"Authorization": "Bearer fixture-webhook-token"},
        json=_webhook_payload("inactive-plan"),
    )

    assert response.status_code == 409
    assert error_code(response) == "plan_not_active"
