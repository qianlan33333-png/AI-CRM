from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.automation.ops_enrollment.application import get_user_ops_audit_events, get_user_ops_side_effect_plans


def test_user_ops_broadcast_preview_is_commandbus_plan_only() -> None:
    client = TestClient(create_app())
    payload = {"filters": {"tag": "黄小璨"}, "message": {"text": "测试预览，不发送"}}

    first = client.post("/api/admin/user-ops/broadcast/preview", json=payload, headers={"Idempotency-Key": "broadcast-preview-1"})
    second = client.post("/api/admin/user-ops/broadcast/preview", json=payload, headers={"Idempotency-Key": "broadcast-preview-1"})

    assert first.status_code == 200
    body = first.json()
    assert second.json()["preview_id"] == body["preview_id"]
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["source_status"] == "next_command"
    assert body["preview_status"] == "controlled_preview"
    assert body["real_external_call_executed"] is False
    assert body["candidate_count"] == 3
    assert body["eligible_count"] == 1
    assert body["excluded_reasons"]
    assert body["message_preview"] == "测试预览，不发送"
    assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
    assert body["side_effect_plan"]["real_external_call_executed"] is False
    assert body["sample_customers"][0]["mobile"] == "138****8000"
    assert get_user_ops_audit_events()
    assert get_user_ops_side_effect_plans()


def test_user_ops_broadcast_preview_accepts_empty_payload_as_controlled_default() -> None:
    response = TestClient(create_app()).post(
        "/api/admin/user-ops/broadcast/preview",
        json={},
        headers={"Idempotency-Key": "broadcast-preview-empty"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["source_status"] == "next_command"
    assert body["preview_status"] == "controlled_default_preview"
    assert body["real_external_call_executed"] is False
    assert body["side_effect_plan"]["real_external_call_executed"] is False
    assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
