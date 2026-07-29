from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.crm.customer_tags.live_mutation import (
    get_wecom_tag_live_mutation_audit_events,
    get_wecom_tag_live_mutation_side_effect_plans,
    live_gate_status,
    tag_execution_status,
)
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("SECRET_KEY", "wecom-live-mutation-command-test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.delenv("AICRM_WECOM_TAG_LIVE_ADAPTER_ENABLED", raising=False)
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_queued_external_effect(payload: dict, command_name: str, effect_type: str) -> None:
    assert payload["ok"] is True
    assert payload["command_name"] == command_name
    assert payload["source_status"] == "next_command"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["adapter_mode"] == "queued_external_effect"
    assert payload["effect_type"] == effect_type
    assert payload["external_effect_status"] == "queued"
    assert payload["real_external_call_executed"] is False
    assert payload["wecom_api_called"] is False
    assert payload["side_effect_plan"]["effect_type"] == effect_type
    assert payload["side_effect_plan"]["adapter_name"] == "wecom_tag"
    assert payload["side_effect_plan"]["adapter_mode"] == "queued_external_effect"
    assert payload["side_effect_plan"]["status"] == "queued"
    assert payload["side_effect_plan"]["requires_approval"] is False
    assert payload["side_effect_plan"]["real_external_call_executed"] is False
    assert payload["side_effect_plan"]["wecom_api_called"] is False


def test_live_gate_reports_projection_and_queue_support(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/admin/wecom/tags/live/gate")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    payload = response.json()
    assert payload["source_status"] == "tag_execution_status"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["adapter_mode"] == "local_projection_and_external_effect"
    assert payload["local_projection_supported"] is True
    assert payload["external_effect_supported"] is True
    assert payload["requires_approval"] is False
    assert payload["available"] is True
    assert payload["blocked"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["wecom_api_called"] is False


def test_live_gate_status_is_deprecated_alias() -> None:
    assert live_gate_status() == tag_execution_status()


def test_live_mark_and_unmark_queue_external_effect_without_approval(monkeypatch) -> None:
    client = _client(monkeypatch)

    mark = client.post(
        "/api/admin/wecom/tags/live/mark",
        json={"external_userid": "wx_ext_001", "tag_ids": ["tag_fixture_active"], "operator": "tester"},
        headers={"Idempotency-Key": "wecom-live-mark-command"},
    )
    unmark = client.post(
        "/api/admin/wecom/tags/live/unmark",
        json={"external_userid": "wx_ext_001", "tag_ids": ["tag_fixture_active"], "operator": "tester"},
        headers={"Idempotency-Key": "wecom-live-unmark-command"},
    )

    assert mark.status_code == 200
    assert unmark.status_code == 200
    _assert_queued_external_effect(mark.json(), "wecom.tag.mark", "wecom.tag.mark")
    _assert_queued_external_effect(unmark.json(), "wecom.tag.unmark", "wecom.tag.unmark")
    assert len(get_wecom_tag_live_mutation_audit_events()) == 2
    assert len(get_wecom_tag_live_mutation_side_effect_plans()) == 2


def test_live_mutation_validation_errors_are_400(monkeypatch) -> None:
    client = _client(monkeypatch)

    missing_external = client.post("/api/admin/wecom/tags/live/mark", json={"tag_ids": ["tag_fixture_active"]})
    missing_tags = client.post("/api/admin/wecom/tags/live/unmark", json={"external_userid": "wx_ext_001", "tag_ids": []})

    assert missing_external.status_code == 400
    assert missing_external.json()["error_code"] == "external_userid_missing"
    assert missing_external.json()["fallback_used"] is False
    assert missing_external.json()["wecom_api_called"] is False
    assert missing_tags.status_code == 400
    assert missing_tags.json()["error_code"] == "tag_ids_missing"
