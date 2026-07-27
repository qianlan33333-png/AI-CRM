from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.crm.customer_tags.admin_write import (
    get_wecom_tag_write_audit_events,
    get_wecom_tag_write_projection_events,
    get_wecom_tag_write_side_effect_plans,
)
from aicrm_next.main import create_app


def test_wecom_tag_write_idempotency_key_reuses_command_result(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "wecom-tag-write-idempotency")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    headers = {"Idempotency-Key": "same-create-tag-key"}
    first = client.post(
        "/api/admin/wecom/tags",
        json={"group_id": "group_fixture_lifecycle", "tag_name": "幂等标签"},
        headers=headers,
    ).json()
    second = client.post(
        "/api/admin/wecom/tags",
        json={"group_id": "group_fixture_lifecycle", "tag_name": "幂等标签"},
        headers=headers,
    ).json()

    assert first["command_id"] == second["command_id"]
    assert first["target_id"] == second["target_id"]
    assert first["idempotency_key"] == "same-create-tag-key"
    assert second["idempotency_key"] == "same-create-tag-key"
    assert [item["write_type"] for item in get_wecom_tag_write_projection_events()].count("tag_created") == 1
    assert len(get_wecom_tag_write_audit_events()) == 1
    assert len(get_wecom_tag_write_side_effect_plans()) == 1
