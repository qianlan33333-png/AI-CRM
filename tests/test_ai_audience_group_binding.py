from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aicrm_next.extensions.ai.ai_audience_ops.automation_binding import AudienceAutomationBindingService
from aicrm_next.extensions.ai.ai_audience_ops.automation_binding.precheck import inspect_automation_bindings
from aicrm_next.platform.shared.db_session import get_session_factory
from tests.admin_auth_test_helpers import admin_session_cookies


def _cookies(client) -> dict[str, str]:
    return admin_session_cookies(client, "super_admin")


def _insert_package(session, package_key: str, name: str, *, group_id: int | None = None) -> int:
    return int(
        session.execute(
            text(
                """
                INSERT INTO ai_audience_package (
                    package_key, name, status, query_mode, identity_policy,
                    incremental_enabled, daily_enabled, incremental_interval_seconds,
                    daily_refresh_time, timezone, lookback_seconds, group_id,
                    created_at, updated_at
                )
                VALUES (
                    :package_key, :name, 'active', 'incremental_event', 'external_userid',
                    TRUE, FALSE, 180, '02:00', 'Asia/Shanghai', 600, :group_id,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                RETURNING id
                """
            ),
            {"package_key": package_key, "name": name, "group_id": group_id},
        ).scalar_one()
    )


def _create_automation(client, *, name: str, code: str, automation_type: str = "agent", status: str = "active") -> dict:
    response = client.post(
        "/api/admin/automation-agents",
        cookies=_cookies(client),
        json={
            "agent_name": name,
            "agent_code": code,
            "automation_type": automation_type,
            "status": status,
            "role_prompt": "你是运营助手" if automation_type == "agent" else "",
            "task_prompt": "输出跟进话术" if automation_type == "agent" else "",
            "fixed_content_package": {"content_text": "固定话术" if automation_type == "fixed_script" else ""},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["agent"]


def test_group_crud_ungrouped_pagination_move_and_copy(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "audience-groups-test")
    created = next_client.post(
        "/api/admin/ai-audience/package-groups",
        cookies=_cookies(next_client),
        json={"name": "Growth 新客"},
    )
    assert created.status_code == 200
    group_id = int(created.json()["group"]["id"])

    duplicate = next_client.post(
        "/api/admin/ai-audience/package-groups",
        cookies=_cookies(next_client),
        json={"name": "gRoWtH 新客"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "group_name_exists"

    with get_session_factory()() as session:
        grouped_id = _insert_package(session, "grouped_pkg", "分组包", group_id=group_id)
        _insert_package(session, "ungrouped_pkg_1", "未分组 1")
        _insert_package(session, "ungrouped_pkg_2", "未分组 2")
        session.commit()

    groups = next_client.get("/api/admin/ai-audience/package-groups", cookies=_cookies(next_client)).json()["items"]
    ungrouped = next(item for item in groups if item["id"] == "ungrouped")
    grouped = next(item for item in groups if item["id"] == group_id)
    assert ungrouped == {"id": "ungrouped", "name": "未分组", "package_count": 2, "is_virtual": True}
    assert grouped["package_count"] == 1

    first_page = next_client.get(
        "/api/admin/ai-audience/packages?group_id=ungrouped&limit=1&offset=0",
        cookies=_cookies(next_client),
    ).json()
    second_page = next_client.get(
        "/api/admin/ai-audience/packages?group_id=ungrouped&limit=1&offset=1",
        cookies=_cookies(next_client),
    ).json()
    assert first_page["total"] == 2
    assert len(first_page["items"]) == len(second_page["items"]) == 1
    assert first_page["items"][0]["id"] != second_page["items"][0]["id"]
    beyond_last_page = next_client.get(
        "/api/admin/ai-audience/packages?group_id=ungrouped&limit=1&offset=99",
        cookies=_cookies(next_client),
    ).json()
    assert beyond_last_page["items"] == []
    assert beyond_last_page["total"] == 2
    missing_group = next_client.get(
        "/api/admin/ai-audience/packages?group_id=999999",
        cookies=_cookies(next_client),
    )
    assert missing_group.status_code == 404
    assert missing_group.json()["error"] == "group_not_found"

    nonempty = next_client.delete(f"/api/admin/ai-audience/package-groups/{group_id}", cookies=_cookies(next_client))
    assert nonempty.status_code == 409
    assert nonempty.json()["error"] == "group_not_empty"

    moved = next_client.patch(
        f"/api/admin/ai-audience/packages/{grouped_id}",
        cookies=_cookies(next_client),
        json={"name": "分组包", "natural_language_definition": "", "refresh_mode": "incremental_3m", "group_id": None},
    )
    assert moved.status_code == 200
    assert moved.json()["package"]["group_id"] is None

    moved_back = next_client.patch(
        f"/api/admin/ai-audience/packages/{grouped_id}",
        cookies=_cookies(next_client),
        json={"name": "分组包", "natural_language_definition": "", "refresh_mode": "incremental_3m", "group_id": group_id},
    )
    assert moved_back.status_code == 200
    copied = next_client.post(
        f"/api/admin/ai-audience/packages/{grouped_id}/copy",
        cookies=_cookies(next_client),
    )
    assert copied.status_code == 200
    assert copied.json()["package"]["group_id"] == group_id
    copied_id = int(copied.json()["package"]["id"])

    renamed = next_client.patch(
        f"/api/admin/ai-audience/package-groups/{group_id}",
        cookies=_cookies(next_client),
        json={"name": "首购转化"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["group"]["name"] == "首购转化"

    for package_id in (grouped_id, copied_id):
        moved_out = next_client.patch(
            f"/api/admin/ai-audience/packages/{package_id}",
            cookies=_cookies(next_client),
            json={"refresh_mode": "incremental_3m", "group_id": None},
        )
        assert moved_out.status_code == 200
        assert moved_out.json()["package"]["group_id"] is None
    deleted = next_client.delete(f"/api/admin/ai-audience/package-groups/{group_id}", cookies=_cookies(next_client))
    assert deleted.status_code == 200
    remaining_ids = {
        item["id"]
        for item in next_client.get("/api/admin/ai-audience/package-groups", cookies=_cookies(next_client)).json()["items"]
    }
    assert group_id not in remaining_ids


def test_binding_one_to_one_status_sync_replace_unbind_and_delete_guards(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "audience-binding-test")
    with get_session_factory()() as session:
        package_a = _insert_package(session, "binding_pkg_a", "绑定包 A")
        package_b = _insert_package(session, "binding_pkg_b", "绑定包 B")
        session.commit()

    agent = _create_automation(next_client, name="问卷 Agent", code="questionnaire_agent")
    fixed = _create_automation(next_client, name="固定欢迎语", code="fixed_welcome", automation_type="fixed_script")
    paused = _create_automation(next_client, name="已停止 Agent", code="paused_agent", status="paused")

    agent_filter = next_client.get("/api/admin/automation-agents?automation_type=agent", cookies=_cookies(next_client)).json()
    fixed_filter = next_client.get("/api/admin/automation-agents?automation_type=fixed_script", cookies=_cookies(next_client)).json()
    assert {item["automation_type"] for item in agent_filter["items"]} == {"agent"}
    assert {item["automation_type"] for item in fixed_filter["items"]} == {"fixed_script"}

    bound = next_client.put(
        f"/api/admin/ai-audience/packages/{package_a}/automation-binding",
        cookies=_cookies(next_client),
        json={"automation_id": agent["id"]},
    )
    assert bound.status_code == 200
    assert bound.json()["binding"]["agent_code"] == "questionnaire_agent"
    assert bound.json()["deduplicated"] is False
    repeated = next_client.put(
        f"/api/admin/ai-audience/packages/{package_a}/automation-binding",
        cookies=_cookies(next_client),
        json={"automation_id": agent["id"]},
    )
    assert repeated.status_code == 200
    assert repeated.json()["deduplicated"] is True

    conflict = next_client.put(
        f"/api/admin/ai-audience/packages/{package_b}/automation-binding",
        cookies=_cookies(next_client),
        json={"automation_id": agent["id"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "automation_already_bound"
    stopped = next_client.put(
        f"/api/admin/ai-audience/packages/{package_b}/automation-binding",
        cookies=_cookies(next_client),
        json={"automation_id": paused["id"]},
    )
    assert stopped.status_code == 409
    assert stopped.json()["error"] == "automation_not_active"

    with get_session_factory()() as session:
        subscription = session.execute(
            text("SELECT status, webhook_url, headers_json, payload_template_json FROM ai_audience_outbound_subscription WHERE package_id = :id"),
            {"id": package_a},
        ).mappings().one()
        audit_count = session.execute(
            text("SELECT COUNT(*) FROM admin_operation_logs WHERE target_type = 'ai_audience_automation_binding' AND target_id = :target_id"),
            {"target_id": str(package_a)},
        ).scalar_one()
    assert subscription["status"] == "active"
    assert subscription["webhook_url"] == "/api/ai/agents/questionnaire_agent/audience-webhook"
    assert subscription["headers_json"] == {}
    assert subscription["payload_template_json"] == {}
    assert audit_count == 1

    paused_response = next_client.post(f"/api/admin/automation-agents/{agent['id']}/pause", cookies=_cookies(next_client))
    assert paused_response.status_code == 200
    binding = next_client.get(
        f"/api/admin/ai-audience/packages/{package_a}/automation-binding",
        cookies=_cookies(next_client),
    ).json()["binding"]
    assert binding["warning"] == "automation_paused"
    with get_session_factory()() as session:
        assert session.execute(
            text("SELECT status FROM ai_audience_outbound_subscription WHERE package_id = :id"),
            {"id": package_a},
        ).scalar_one() == "paused"

    assert next_client.post(f"/api/admin/automation-agents/{agent['id']}/activate", cookies=_cookies(next_client)).status_code == 200
    replaced = next_client.put(
        f"/api/admin/ai-audience/packages/{package_a}/automation-binding",
        cookies=_cookies(next_client),
        json={"automation_id": fixed["id"]},
    )
    assert replaced.status_code == 200
    assert replaced.json()["binding"]["automation_type"] == "fixed_script"
    with get_session_factory()() as session:
        old_bound = session.execute(text("SELECT bound_package_key FROM automation_agent_runtime_config WHERE id = :id"), {"id": agent["id"]}).scalar_one()
        active_urls = session.execute(
            text("SELECT webhook_url FROM ai_audience_outbound_subscription WHERE package_id = :id AND status = 'active'"),
            {"id": package_a},
        ).scalars().all()
    assert old_bound == ""
    assert active_urls == ["/api/ai/agents/fixed_welcome/audience-webhook"]

    copied = next_client.post(f"/api/admin/automation-agents/{fixed['id']}/copy", cookies=_cookies(next_client))
    assert copied.status_code == 200
    assert copied.json()["agent"]["bound_package_key"] == ""
    assert next_client.delete(f"/api/admin/automation-agents/{fixed['id']}", cookies=_cookies(next_client)).status_code == 409
    assert next_client.delete(f"/api/admin/ai-audience/packages/{package_a}", cookies=_cookies(next_client)).status_code == 409

    unbound = next_client.delete(
        f"/api/admin/ai-audience/packages/{package_a}/automation-binding",
        cookies=_cookies(next_client),
    )
    assert unbound.status_code == 200
    assert unbound.json()["binding"] is None
    assert next_client.delete(f"/api/admin/automation-agents/{fixed['id']}", cookies=_cookies(next_client)).status_code == 200
    assert next_client.delete(f"/api/admin/ai-audience/packages/{package_a}", cookies=_cookies(next_client)).status_code == 200


def test_binding_database_unique_constraint_and_concurrent_idempotency(next_pg_schema) -> None:
    del next_pg_schema
    with get_session_factory()() as session:
        package_id = _insert_package(session, "concurrent_pkg", "并发绑定包")
        first_id = session.execute(
            text(
                """
                INSERT INTO automation_agent_runtime_config (
                    agent_code, agent_name, automation_type, bound_package_key, status,
                    draft_role_prompt, draft_task_prompt, published_role_prompt, published_task_prompt,
                    draft_version, published_version, fixed_content_package_json, send_webhook_url,
                    created_at, updated_at
                ) VALUES (
                    'concurrent_agent', '并发 Agent', 'agent', '', 'active', '', '', '', '',
                    1, 1, '{}'::jsonb, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                ) RETURNING id
                """
            )
        ).scalar_one()
        session.commit()

    def bind_once() -> dict:
        return AudienceAutomationBindingService().put(package_id, int(first_id), operator="pytest_concurrent")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: bind_once(), range(2)))
    assert all(result["ok"] for result in results)
    assert sorted(bool(result["deduplicated"]) for result in results) == [False, True]

    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO automation_agent_runtime_config (
                    agent_code, agent_name, automation_type, bound_package_key, status,
                    draft_role_prompt, draft_task_prompt, published_role_prompt, published_task_prompt,
                    draft_version, published_version, fixed_content_package_json, send_webhook_url,
                    created_at, updated_at
                ) VALUES (
                    'unique_conflict_agent', '唯一冲突 Agent', 'agent', '', 'active', '', '', '', '',
                    1, 1, '{}'::jsonb, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        session.commit()
    with pytest.raises(IntegrityError):
        with get_session_factory()() as session:
            session.execute(
                text(
                    """
                    UPDATE automation_agent_runtime_config
                    SET bound_package_key = 'concurrent_pkg'
                    WHERE agent_code = 'unique_conflict_agent'
                    """
                )
            )
            session.commit()


def test_binding_precheck_reports_external_urls_and_clean_inference(next_pg_schema) -> None:
    del next_pg_schema
    with get_session_factory()() as session:
        package_id = _insert_package(session, "precheck_pkg", "迁移检查包")
        session.execute(
            text(
                """
                INSERT INTO automation_agent_runtime_config (
                    agent_code, agent_name, automation_type, bound_package_key, status,
                    draft_role_prompt, draft_task_prompt, published_role_prompt, published_task_prompt,
                    draft_version, published_version, fixed_content_package_json, send_webhook_url,
                    created_at, updated_at
                ) VALUES (
                    'precheck_agent', '迁移检查 Agent', 'agent', 'precheck_pkg', 'active', '', '', '', '',
                    1, 1, '{}'::jsonb, 'https://outside.example/audience', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        session.commit()
        report = inspect_automation_bindings(session.connection())
    assert report.ok is False
    assert any(item["kind"] == "external_or_invalid_send_url" for item in report.issues)

    with get_session_factory()() as session:
        session.execute(
            text("UPDATE automation_agent_runtime_config SET send_webhook_url = '/api/ai/audience/packages/precheck_pkg/webhook' WHERE agent_code = 'precheck_agent'")
        )
        session.execute(
            text(
                """
                INSERT INTO ai_audience_outbound_subscription (
                    package_id, status, trigger_event_type, dispatch_mode, target_type,
                    webhook_url, headers_json, payload_template_json, execution_mode,
                    requires_approval, max_attempts, created_at, updated_at
                ) VALUES (
                    :package_id, 'active', 'entered', 'per_run', 'webhook',
                    '/api/ai/agents/precheck_agent/audience-webhook', '{}'::jsonb, '{}'::jsonb,
                    'execute', FALSE, 5, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"package_id": package_id},
        )
        session.commit()
        clean = inspect_automation_bindings(session.connection())
    assert clean.ok is True
    assert clean.bindings[0]["agent_code"] == "precheck_agent"
    assert clean.bindings[0]["package_key"] == "precheck_pkg"
