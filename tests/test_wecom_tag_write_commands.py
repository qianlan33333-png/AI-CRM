from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.crm.customer_tags.admin_write import (
    get_wecom_tag_write_audit_events,
    get_wecom_tag_write_projection_events,
    get_wecom_tag_write_side_effect_plans,
)
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("SECRET_KEY", "wecom-tag-write-command-test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_next_command(payload: dict, command_name: str) -> None:
    assert payload["ok"] is True
    assert payload["command_name"] == command_name
    assert payload["source_status"] == "next_command"
    assert payload["write_model_status"] == "local_projection_updated"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["local_only"] is True
    assert payload["audit_recorded"] is True
    assert payload["side_effect_plan"]["adapter_mode"] == "real_blocked"
    assert payload["side_effect_plan"]["requires_approval"] is True
    assert payload["side_effect_plan"]["real_external_call_executed"] is False


def test_wecom_tag_crud_routes_execute_next_commands(monkeypatch) -> None:
    client = _client(monkeypatch)

    create = client.post(
        "/api/admin/wecom/tags",
        json={"group_id": "group_fixture_lifecycle", "tag_name": "第13组标签"},
        headers={"Idempotency-Key": "tag-create-13"},
    ).json()
    _assert_next_command(create, "wecom.tag.create")
    tag_id = create["tag"]["tag_id"]

    update = client.patch(f"/api/admin/wecom/tags/{tag_id}", json={"tag_name": "第13组标签更新"}).json()
    _assert_next_command(update, "wecom.tag.update")
    assert update["tag"]["tag_name"] == "第13组标签更新"

    delete = client.delete(f"/api/admin/wecom/tags/{tag_id}").json()
    _assert_next_command(delete, "wecom.tag.delete")
    assert delete["deleted"] is True

    write_types = {item["write_type"] for item in get_wecom_tag_write_projection_events()}
    assert {"tag_created", "tag_updated", "tag_deleted"}.issubset(write_types)


def test_wecom_tag_group_crud_and_sync_routes_execute_next_commands(monkeypatch) -> None:
    client = _client(monkeypatch)

    create = client.post(
        "/api/admin/wecom/tag-groups",
        json={"group_name": "第13组标签组", "first_tag_name": "首个标签"},
        headers={"Idempotency-Key": "group-create-13"},
    ).json()
    _assert_next_command(create, "wecom.tag_group.create")
    group_id = create["group"]["group_id"]
    assert create["tags"]

    update = client.put(f"/api/admin/wecom/tag-groups/{group_id}", json={"group_name": "第13组标签组更新"}).json()
    _assert_next_command(update, "wecom.tag_group.update")
    assert update["group"]["group_name"] == "第13组标签组更新"

    delete = client.delete(f"/api/admin/wecom/tag-groups/{group_id}").json()
    _assert_next_command(delete, "wecom.tag_group.delete")
    assert delete["deleted"] is True

    assert len(get_wecom_tag_write_audit_events()) >= 3
    assert len(get_wecom_tag_write_side_effect_plans()) >= 3


def test_wecom_tag_write_validation_errors_are_controlled(monkeypatch) -> None:
    client = _client(monkeypatch)

    bad_create = client.post("/api/admin/wecom/tags", json={"group_id": "", "tag_name": "x"})
    missing = client.patch("/api/admin/wecom/tags/missing_tag", json={"tag_name": "x"})

    assert bad_create.status_code == 400
    assert bad_create.json()["source_status"] == "next_command"
    assert bad_create.json()["error_code"] == "input_error"
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "not_found"
