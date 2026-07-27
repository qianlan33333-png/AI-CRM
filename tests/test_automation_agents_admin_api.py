from __future__ import annotations

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory
from tests.admin_auth_test_helpers import admin_session_cookies


def _admin_cookies(next_client) -> dict[str, str]:
    return admin_session_cookies(next_client, "super_admin")


def _create_payload(**overrides):
    payload = {
        "agent_name": "9.9问卷激活跟进 Agent",
        "agent_code": "questionnaire_activation_agent",
        "bound_package_key": "prod_channel_9p9_questionnaire_activation_hyc",
        "role_prompt": "你是私域运营助手",
        "task_prompt": "根据{{问卷信息}}生成一条跟进话术",
    }
    payload.update(overrides)
    return payload


def test_admin_automation_agents_requires_admin_session(next_client, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    response = next_client.get("/api/admin/automation-agents")

    assert response.status_code == 401
    assert response.json()["error"] == "admin_auth_required"


def test_admin_automation_agent_crud_copy_pause_archive_contract(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "automation-agent-admin-test")

    created = next_client.post(
        "/api/admin/automation-agents",
        json=_create_payload(),
        cookies=_admin_cookies(next_client),
    )
    assert created.status_code == 200
    agent = created.json()["agent"]
    agent_id = agent["id"]
    assert agent["receive_webhook_url"].endswith("/api/ai/agents/questionnaire_activation_agent/audience-webhook")
    assert agent["receive_webhook_auth_mode"] == "aicrm_hmac_sha256"
    assert agent["send_webhook_url"].endswith("/api/ai/audience/packages/prod_channel_9p9_questionnaire_activation_hyc/webhook")
    assert agent["automation_type"] == "agent"
    assert agent["automation_type_label"] == "agent"
    assert agent["fixed_material_summary"] == {"image_count": 0, "miniprogram_count": 0, "attachment_count": 0, "group_invite_count": 0}

    listed = next_client.get("/api/admin/automation-agents", cookies=_admin_cookies(next_client))
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["agent_code"] == "questionnaire_activation_agent"
    assert item["automation_type"] == "agent"
    assert "receive_format" not in item
    assert "receive_send_address" not in item

    detail = next_client.get(f"/api/admin/automation-agents/{agent_id}", cookies=_admin_cookies(next_client))
    assert detail.status_code == 200
    assert detail.json()["agent"]["published_task_prompt"] == "根据{{问卷信息}}生成一条跟进话术"

    patched = next_client.patch(
        f"/api/admin/automation-agents/{agent_id}",
        json={
            "agent_name": "更新后的 Agent",
            "task_prompt": "看{{用户标签}}输出话术",
            "send_webhook_url": "https://www.youcangogogo.com/api/ai/audience/packages/prod_channel_9p9_questionnaire_activation_hyc/webhook",
        },
        cookies=_admin_cookies(next_client),
    )
    assert patched.status_code == 200
    assert patched.json()["agent"]["agent_name"] == "更新后的 Agent"
    assert patched.json()["agent"]["draft_task_prompt"] == "看{{用户标签}}输出话术"
    assert patched.json()["agent"]["published_task_prompt"] == "根据{{问卷信息}}生成一条跟进话术"
    assert patched.json()["agent"]["draft_version"] == 2
    assert patched.json()["agent"]["published_version"] == 1
    assert patched.json()["agent"]["has_unpublished_changes"] is True
    assert (
        patched.json()["agent"]["send_webhook_url"]
        == "https://www.youcangogogo.com/api/ai/audience/packages/prod_channel_9p9_questionnaire_activation_hyc/webhook"
    )

    invalid_send = next_client.patch(
        f"/api/admin/automation-agents/{agent_id}",
        json={"send_webhook_url": "https://example.com/custom/webhook"},
        cookies=_admin_cookies(next_client),
    )
    assert invalid_send.status_code == 400
    assert invalid_send.json()["error"] == "invalid_send_webhook_url"

    published = next_client.post(
        f"/api/admin/automation-agents/{agent_id}/publish",
        cookies=_admin_cookies(next_client),
    )
    assert published.status_code == 200
    assert published.json()["agent"]["published_task_prompt"] == "看{{用户标签}}输出话术"
    assert published.json()["agent"]["published_version"] == 2
    assert published.json()["agent"]["has_unpublished_changes"] is False

    republished = next_client.post(
        f"/api/admin/automation-agents/{agent_id}/publish",
        cookies=_admin_cookies(next_client),
    )
    assert republished.status_code == 200
    assert republished.json()["agent"]["published_version"] == 2

    removed_reset = next_client.post(
        f"/api/admin/automation-agents/{agent_id}/reset-token",
        cookies=_admin_cookies(next_client),
    )
    assert removed_reset.status_code == 404

    copied = next_client.post(f"/api/admin/automation-agents/{agent_id}/copy", cookies=_admin_cookies(next_client))
    assert copied.status_code == 200
    assert copied.json()["agent"]["agent_code"] == "questionnaire_activation_agent_copy_001"
    assert copied.json()["agent"]["receive_webhook_url"] != patched.json()["agent"]["receive_webhook_url"]

    paused = next_client.post(f"/api/admin/automation-agents/{agent_id}/pause", cookies=_admin_cookies(next_client))
    assert paused.status_code == 200
    assert paused.json()["agent"]["status"] == "paused"
    activated = next_client.post(f"/api/admin/automation-agents/{agent_id}/activate", cookies=_admin_cookies(next_client))
    assert activated.status_code == 200
    assert activated.json()["agent"]["status"] == "active"

    archived = next_client.delete(f"/api/admin/automation-agents/{agent_id}", cookies=_admin_cookies(next_client))
    assert archived.status_code == 200
    listed_after = next_client.get("/api/admin/automation-agents", cookies=_admin_cookies(next_client)).json()
    assert all(item["id"] != agent_id for item in listed_after["items"])


def test_fixed_content_normalizes_and_rejects_non_pdf_attachment(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "automation-agent-fixed-content-test")
    created = next_client.post(
        "/api/admin/automation-agents",
        json=_create_payload(agent_code="fixed_agent"),
        cookies=_admin_cookies(next_client),
    )
    agent_id = created.json()["agent"]["id"]

    ok = next_client.put(
        f"/api/admin/automation-agents/{agent_id}/fixed-content",
        json={"content_package": {"content_text": "ignored", "image_library_ids": [12, "12"], "miniprogram_library_ids": [34], "attachment_library_ids": []}},
        cookies=_admin_cookies(next_client),
    )
    assert ok.status_code == 200
    assert ok.json()["agent"]["fixed_content_package"]["content_text"] == ""
    assert ok.json()["agent"]["fixed_content_package"]["image_library_ids"] == [12]

    too_many = next_client.put(
        f"/api/admin/automation-agents/{agent_id}/fixed-content",
        json={"content_package": {"image_library_ids": [1, 2, 3, 4]}},
        cookies=_admin_cookies(next_client),
    )
    assert too_many.status_code == 400
    assert too_many.json()["error"] == "invalid_fixed_content_package"

    class FakeRepo:
        def get_materials_by_ids(self, material_type, ids):
            return [{"library_id": ids[0], "metadata": {"mime_type": "text/plain"}}]

    from aicrm_next.extensions.ai.automation_agents import application as app_module

    monkeypatch.setattr(app_module, "build_send_content_repository", lambda: FakeRepo())
    non_pdf = next_client.put(
        f"/api/admin/automation-agents/{agent_id}/fixed-content",
        json={"content_package": {"attachment_library_ids": [9]}},
        cookies=_admin_cookies(next_client),
    )
    assert non_pdf.status_code == 400
    assert non_pdf.json()["error"] == "invalid_fixed_content_package"


def test_fixed_script_preserves_content_text(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "automation-agent-fixed-script-test")
    created = next_client.post(
        "/api/admin/automation-agents",
        json=_create_payload(
            agent_name="固定问卷话术",
            agent_code="fixed_script_agent",
            automation_type="fixed_script",
            role_prompt="",
            task_prompt="",
            fixed_content_package={"content_text": "固定发送的话术", "image_library_ids": [12]},
        ),
        cookies=_admin_cookies(next_client),
    )
    assert created.status_code == 200
    agent = created.json()["agent"]
    assert agent["automation_type"] == "fixed_script"
    assert agent["automation_type_label"] == "固定话术"
    assert agent["fixed_content_package"]["content_text"] == "固定发送的话术"

    agent_id = agent["id"]
    saved = next_client.put(
        f"/api/admin/automation-agents/{agent_id}/fixed-content",
        json={"content_package": {"content_text": "更新后的固定话术", "image_library_ids": [12, "12"]}},
        cookies=_admin_cookies(next_client),
    )
    assert saved.status_code == 200
    assert saved.json()["agent"]["fixed_content_package"]["content_text"] == "更新后的固定话术"
    assert saved.json()["agent"]["fixed_content_package"]["image_library_ids"] == [12]

    copied = next_client.post(f"/api/admin/automation-agents/{agent_id}/copy", cookies=_admin_cookies(next_client))
    assert copied.status_code == 200
    assert copied.json()["agent"]["automation_type"] == "fixed_script"


def test_admin_automation_agent_rows_are_soft_deleted(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "automation-agent-soft-delete-test")
    created = next_client.post(
        "/api/admin/automation-agents",
        json=_create_payload(agent_code="soft_delete_agent"),
        cookies=_admin_cookies(next_client),
    )
    agent_id = created.json()["agent"]["id"]

    next_client.delete(f"/api/admin/automation-agents/{agent_id}", cookies=_admin_cookies(next_client))

    with get_session_factory()() as session:
        row = (
            session.execute(
                text("SELECT status, archived_at FROM automation_agent_runtime_config WHERE id = :id"),
                {"id": agent_id},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "archived"
    assert row["archived_at"] is not None
