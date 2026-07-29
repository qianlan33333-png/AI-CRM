from __future__ import annotations

from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, WECOM_MESSAGE_GROUP_SEND
from tests.group_ops_test_helpers import error_code, group_ops_api_client


def _wecom_group_jobs():
    return ExternalEffectService().list_jobs({"effect_type": WECOM_MESSAGE_GROUP_SEND}, limit=20)[0]


def test_standard_run_due_preview_does_not_enqueue_broadcast_jobs(group_ops_api_client, monkeypatch):
    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/run-due/preview",
        json={"operator": "pytest", "max_outbound_tasks": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "preview"
    assert body["items"][0]["content_payload"]["channel"] == "wecom_customer_group"
    assert body["items"][0]["content_payload"]["sender"] == "owner_001"
    assert body["items"][0]["chat_ids"] == ["wrOgAAA001", "wrOgAAA002"]


def test_standard_run_due_requires_allowlist_before_queue(group_ops_api_client, monkeypatch):
    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/run-due",
        json={"operator": "pytest", "max_outbound_tasks": 10},
    )

    assert response.status_code == 400
    assert error_code(response) == "allowlist_required"


def test_standard_run_due_with_allow_node_ids_creates_external_effect_job(group_ops_api_client, monkeypatch):
    monkeypatch.setenv("AICRM_GROUP_OPS_OUTBOUND_MODE", "shadow")

    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/run-due",
        json={"operator": "pytest", "allow_node_ids": [1], "max_outbound_tasks": 1},
    )

    assert response.status_code == 202
    body = response.json()
    jobs = _wecom_group_jobs()
    assert body["broadcast_job_ids"] == []
    assert body["legacy_broadcast_job_ids"] == []
    assert body["external_effect_job_ids"] == [jobs[0].id]
    assert body["outbound_mode"] == "external_effect"
    assert jobs[0].payload_json["chat_ids"] == ["wrOgAAA001", "wrOgAAA002"]
    assert jobs[0].payload_json["content_payload"]["channel"] == "wecom_customer_group"
    assert jobs[0].payload_json["content_payload"]["sender"] == "owner_001"
