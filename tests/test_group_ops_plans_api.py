from __future__ import annotations

from aicrm_next.shared.repository_provider import RepositoryProviderError
from tests.group_ops_test_helpers import group_ops_api_client


class FixedQueueStatsGateway:
    def count_group_ops_queue(self) -> int:
        return 7


class DetailRepoWithMissingOptionalTables:
    source_status = "postgres_group_ops_repository"

    def get_plan(self, plan_id: int) -> dict:
        return {
            "id": int(plan_id),
            "plan_name": "生产详情兼容计划",
            "plan_type": "standard",
            "owner_userid": "owner_001",
            "owner_name": "Owner",
            "status": "draft",
            "created_at": "2026-06-01T00:00:00",
            "updated_at": "2026-06-01T00:00:00",
        }

    def list_bound_groups(self, plan_id: int) -> list[dict]:
        return []

    def list_nodes(self, plan_id: int) -> list[dict]:
        return []

    def list_plan_scopes(self, plan_id: int) -> list[dict]:
        raise RepositoryProviderError("group ops repository unavailable: relation automation_group_ops_plan_scope does not exist")

    def get_segmentation(self, plan_id: int) -> dict | None:
        raise RepositoryProviderError("group ops repository unavailable: relation automation_group_ops_plan_segmentation does not exist")

    def list_execution_logs(self, plan_id: int, filters: dict) -> tuple[list[dict], int]:
        raise RepositoryProviderError("group ops repository unavailable: relation automation_group_ops_execution_log does not exist")


def test_group_ops_admin_api_routes_are_registered_on_existing_contracts(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    from aicrm_next.main import create_app

    app = create_app()
    registered = {(method, getattr(route, "path", "")) for route in app.routes for method in getattr(route, "methods", set())}

    expected = {
        ("GET", "/api/admin/automation-conversion/group-ops/plans"),
        ("POST", "/api/admin/automation-conversion/group-ops/plans"),
        ("GET", "/api/admin/automation-conversion/group-ops/plans/{plan_id}"),
        ("PUT", "/api/admin/automation-conversion/group-ops/plans/{plan_id}"),
        ("POST", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/enable"),
        ("POST", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/disable"),
        ("DELETE", "/api/admin/automation-conversion/group-ops/plans/{plan_id}"),
        ("GET", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/groups"),
        ("POST", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/groups"),
        ("DELETE", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/groups/{chat_id}"),
        ("GET", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/nodes"),
        ("POST", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/nodes"),
        ("PUT", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/nodes/{node_id}"),
        ("DELETE", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/nodes/{node_id}"),
        ("GET", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/webhook"),
        ("GET", "/api/admin/automation-conversion/group-ops/groups"),
        ("POST", "/api/admin/automation-conversion/group-ops/groups/sync"),
        ("GET", "/api/admin/common/operation-members"),
    }

    assert expected <= registered
    assert ("PATCH", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/webhook") not in registered


def test_plan_list_returns_plan_fields_without_next_action(group_ops_api_client):
    response = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    plan_types = [item["plan_type"] for item in body["items"]]
    assert plan_types.count("standard") == 2
    assert plan_types.count("webhook") == 1
    required = {
        "plan_name",
        "plan_type",
        "owner_name",
        "bound_group_count",
        "today_estimated_reach",
        "status",
    }
    for item in body["items"]:
        assert required <= set(item)
        assert "next_action" not in item


def test_plan_detail_tolerates_missing_optional_webhook_rule_tables():
    from aicrm_next.automation_engine.group_ops.application import GetGroupOpsPlanQuery

    payload = GetGroupOpsPlanQuery(repo=DetailRepoWithMissingOptionalTables())(7)

    assert payload["ok"] is True
    assert payload["item"]["id"] == 7
    assert payload["plan"]["boundGroupIds"] == []
    assert payload["plan"]["boundAudienceIds"] == []
    assert payload["plan"]["segmentation"] == {}
    assert payload["plan"]["segmentationStats"] == {"total": 0, "layers": []}
    assert payload["plan"]["executionStats"] == {"total": 0, "lastStatus": ""}


def test_group_ops_detail_api_regression_keeps_existing_business_endpoints(group_ops_api_client):
    listed = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans")
    detail = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans/1")
    assert listed.status_code == 200
    assert detail.status_code == 200

    current = detail.json()["item"]
    updated_plan = group_ops_api_client.put(
        "/api/admin/automation-conversion/group-ops/plans/1",
        json={
            "plan_name": "pytest 更新群运营计划",
            "plan_code": current["plan_code"],
            "plan_type": current["plan_type"],
            "owner_userid": current["owner_userid"],
            "status": "draft",
        },
    )
    assert updated_plan.status_code == 200
    assert updated_plan.json()["item"]["plan_name"] == "pytest 更新群运营计划"

    groups = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans/1/groups")
    bind = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/groups",
        json={"chat_id": "wrOgAAA003", "operator": "pytest"},
    )
    unbind = group_ops_api_client.delete("/api/admin/automation-conversion/group-ops/plans/1/groups/wrOgAAA003")
    assert groups.status_code == 200
    assert bind.status_code == 201
    assert unbind.status_code == 200

    webhook_config = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans/2/webhook")
    assert webhook_config.status_code == 200
    assert webhook_config.json()["method"] == "POST"
    assert webhook_config.json()["auth_mode"] == "aicrm_hmac_sha256"

    nodes = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans/1/nodes")
    created_node = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/nodes",
        json={
            "day_index": 2,
            "scheduled_time": "10:00",
            "action_title": "pytest 回归动作",
            "text_content": "pytest 回归动作正文",
            "sort_order": 90,
            "status": "active",
        },
    )
    assert nodes.status_code == 200
    assert created_node.status_code == 201
    node_id = created_node.json()["item"]["id"]

    updated_node = group_ops_api_client.put(
        f"/api/admin/automation-conversion/group-ops/plans/1/nodes/{node_id}",
        json={
            "day_index": 2,
            "scheduled_time": "10:30",
            "action_title": "pytest 更新动作",
            "text_content": "pytest 更新动作正文",
            "sort_order": 91,
            "status": "active",
        },
    )
    deleted_node = group_ops_api_client.delete(f"/api/admin/automation-conversion/group-ops/plans/1/nodes/{node_id}")
    assert updated_node.status_code == 200
    assert updated_node.json()["item"]["scheduled_time"] == "10:30"
    assert deleted_node.status_code == 200


def test_plan_list_returns_group_ops_queue_count(group_ops_api_client, monkeypatch):
    from aicrm_next import integration_ports

    monkeypatch.setattr(integration_ports, "build_group_ops_queue_stats_gateway", lambda: FixedQueueStatsGateway())

    response = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans")

    assert response.status_code == 200
    assert response.json()["queue_count"] == 7


def test_admin_plan_actions_can_disable_enable_and_delete(group_ops_api_client):
    created = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={"plan_name": "pytest 操作按钮计划", "plan_type": "standard", "owner_userid": "owner_001", "status": "active"},
    )
    assert created.status_code == 201
    plan_id = created.json()["item"]["id"]

    disabled = group_ops_api_client.post(f"/api/admin/automation-conversion/group-ops/plans/{plan_id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["item"]["status"] == "disabled"

    enabled = group_ops_api_client.post(f"/api/admin/automation-conversion/group-ops/plans/{plan_id}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["item"]["status"] == "active"

    disabled_again = group_ops_api_client.post(f"/api/admin/automation-conversion/group-ops/plans/{plan_id}/disable")
    assert disabled_again.status_code == 200
    assert disabled_again.json()["item"]["status"] == "disabled"

    deleted = group_ops_api_client.delete(f"/api/admin/automation-conversion/group-ops/plans/{plan_id}")
    assert deleted.status_code == 200
    assert deleted.json()["archived"] is True

    detail = group_ops_api_client.get(f"/api/admin/automation-conversion/group-ops/plans/{plan_id}")
    listed = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans")
    assert detail.status_code == 404
    assert plan_id not in [item["id"] for item in listed.json()["items"]]


def test_owners_api_returns_multiple_fixture_owners(group_ops_api_client):
    response = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/owners")

    assert response.status_code == 200
    items = response.json()["items"]
    by_userid = {item["userid"]: item for item in items}
    assert {"owner_001", "owner_002", "admin_001"} <= set(by_userid)
    assert by_userid["owner_001"]["name"] == "王小明"
    assert by_userid["owner_002"]["group_count"] >= 1
    assert by_userid["admin_001"]["group_count"] == 0


def test_create_standard_plan_uses_requested_owner_and_type(group_ops_api_client):
    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={"plan_name": "owner_002 标准计划", "plan_type": "standard", "owner_userid": "owner_002", "status": "draft"},
    )
    plan_id = response.json()["item"]["id"]
    detail = group_ops_api_client.get(f"/api/admin/automation-conversion/group-ops/plans/{plan_id}")

    assert response.status_code == 201
    assert response.json()["item"]["plan_type"] == "standard"
    assert response.json()["item"]["owner_userid"] == "owner_002"
    assert detail.status_code == 200
    assert detail.json()["item"]["owner_userid"] == "owner_002"


def test_create_webhook_plan_returns_webhook_and_config(group_ops_api_client):
    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={"plan_name": "owner_002 Webhook 计划", "plan_type": "webhook", "owner_userid": "owner_002", "status": "draft"},
    )
    plan = response.json()["item"]
    config = group_ops_api_client.get(f"/api/admin/automation-conversion/group-ops/plans/{plan['id']}/webhook")

    assert response.status_code == 201
    assert plan["plan_type"] == "webhook"
    assert plan["owner_userid"] == "owner_002"
    assert config.status_code == 200
    assert config.json()["method"] == "POST"
    assert "webhook_url" in config.json()


def test_plan_group_binding_allows_owner_or_group_admin_groups(group_ops_api_client):
    ok_response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/groups",
        json={"chat_id": "wrOgAAA003", "operator": "pytest"},
    )
    assert ok_response.status_code == 201
    assert ok_response.json()["summary"]["bound_group_count"] == 3

    admin_plan = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={"plan_name": "群管理员计划", "plan_type": "standard", "owner_userid": "admin_001", "status": "draft"},
    )
    admin_groups = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/groups?owner_userid=admin_001")
    admin_response = group_ops_api_client.post(
        f"/api/admin/automation-conversion/group-ops/plans/{admin_plan.json()['item']['id']}/groups",
        json={"chat_id": "wrOgBBB001", "operator": "pytest"},
    )
    assert admin_plan.status_code == 201
    assert admin_groups.status_code == 200
    assert [item["chat_id"] for item in admin_groups.json()["items"]] == ["wrOgBBB001"]
    assert admin_groups.json()["items"][0]["admin_userids"] == ["admin_001"]
    assert admin_response.status_code == 201

    bad_response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/groups",
        json={"chat_id": "wrOgBBB001", "operator": "pytest"},
    )
    assert bad_response.status_code == 201
    assert bad_response.json()["item"]["chat_id"] == "wrOgBBB001"


def test_standard_plan_nodes_save_and_list_in_domain_order(group_ops_api_client):
    created_plan = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={
            "plan_name": "pytest 三日群运营",
            "plan_type": "standard",
            "owner_userid": "owner_001",
            "status": "draft",
            "operator": "pytest",
        },
    )
    assert created_plan.status_code == 201
    plan_id = created_plan.json()["item"]["id"]

    nodes = [
        (3, "20:00", "第三天复盘", 30),
        (1, "08:00", "欢迎语 + 课程入口", 10),
        (2, "12:30", "第二天提醒", 20),
    ]
    for day_index, scheduled_time, action_title, sort_order in nodes:
        response = group_ops_api_client.post(
            f"/api/admin/automation-conversion/group-ops/plans/{plan_id}/nodes",
            json={
                "day_index": day_index,
                "scheduled_time": scheduled_time,
                "action_title": action_title,
                "text_content": f"{action_title}正文",
                "attachments": [
                    {
                        "msgtype": "miniprogram",
                        "miniprogram": {
                            "appid": "wx123",
                            "page": "/pages/course/today",
                            "title": "课程入口",
                            "pic_media_id": "MEDIA_ID",
                        },
                    }
                ],
                "sort_order": sort_order,
                "status": "active",
            },
        )
        assert response.status_code == 201

    listed = group_ops_api_client.get(f"/api/admin/automation-conversion/group-ops/plans/{plan_id}/nodes")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert [item["day_index"] for item in items] == [1, 2, 3]
    assert [item["scheduled_time"] for item in items] == ["08:00", "12:30", "20:00"]
    for item in items:
        assert {
            "day_index",
            "scheduled_time",
            "action_title",
            "text_content",
            "attachments",
        } <= set(item)
        assert item["attachments"][0]["msgtype"] == "miniprogram"


def test_standard_plan_nodes_accept_content_package_for_drafts_and_materials(group_ops_api_client):
    created_plan = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={
            "plan_name": "pytest 内容包群运营",
            "plan_type": "standard",
            "owner_userid": "owner_001",
            "status": "draft",
            "operator": "pytest",
        },
    )
    assert created_plan.status_code == 201
    plan_id = created_plan.json()["item"]["id"]

    empty_draft = group_ops_api_client.post(
        f"/api/admin/automation-conversion/group-ops/plans/{plan_id}/nodes",
        json={
            "day_index": 1,
            "scheduled_time": "10:00",
            "action_title": "空内容草稿",
            "content_package_json": {},
            "sort_order": 10,
            "status": "draft",
        },
    )
    assert empty_draft.status_code == 201
    assert empty_draft.json()["item"]["content_package_json"] == {
        "content_text": "",
        "image_library_ids": [],
        "miniprogram_library_ids": [],
        "attachment_library_ids": [],
        "group_invite_library_ids": [],
    }

    text_only = group_ops_api_client.post(
        f"/api/admin/automation-conversion/group-ops/plans/{plan_id}/nodes",
        json={
            "day_index": 1,
            "scheduled_time": "11:00",
            "action_title": "只有话术",
            "content_package_json": {"content_text": "  课程提醒  "},
            "sort_order": 20,
            "status": "active",
        },
    )
    assert text_only.status_code == 201
    assert text_only.json()["item"]["text_content"] == "课程提醒"

    material_only = group_ops_api_client.post(
        f"/api/admin/automation-conversion/group-ops/plans/{plan_id}/nodes",
        json={
            "day_index": 2,
            "scheduled_time": "12:00",
            "action_title": "只有素材",
            "content_package_json": {
                "image_library_ids": [12, "12", 34],
                "miniprogram_library_ids": [56],
                "attachment_library_ids": [78, 90],
                "group_invite_library_ids": [91],
            },
            "sort_order": 30,
            "status": "active",
        },
    )
    assert material_only.status_code == 201
    material_item = material_only.json()["item"]
    assert material_item["text_content"] == ""
    assert material_item["attachments"] == []
    assert material_item["content_package_json"] == {
        "content_text": "",
        "image_library_ids": [12, 34],
        "miniprogram_library_ids": [56],
        "attachment_library_ids": [78, 90],
        "group_invite_library_ids": [91],
    }


def test_standard_plan_nodes_keep_legacy_attachments_compatible(group_ops_api_client):
    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/nodes",
        json={
            "day_index": 3,
            "scheduled_time": "10:00",
            "action_title": "旧附件兼容",
            "text_content": "旧节点话术",
            "attachments": [
                {
                    "msgtype": "miniprogram",
                    "miniprogram": {
                        "appid": "wx123",
                        "page": "/pages/course/today",
                        "title": "课程入口",
                        "pic_media_id": "MEDIA_ID",
                    },
                }
            ],
            "sort_order": 40,
            "status": "active",
        },
    )
    assert response.status_code == 201
    item = response.json()["item"]
    assert item["content_package_json"]["content_text"] == "旧节点话术"
    assert item["attachments"][0]["msgtype"] == "miniprogram"


def test_standard_plan_node_update_saves_standard_content_package(group_ops_api_client):
    created = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/nodes",
        json={
            "day_index": 4,
            "scheduled_time": "10:00",
            "action_title": "历史附件动作",
            "text_content": "老话术",
            "attachments": [
                {
                    "msgtype": "file",
                    "file": {"media_id": "legacy-file-media", "name": "历史附件.pdf"},
                }
            ],
            "sort_order": 70,
            "status": "active",
        },
    )
    assert created.status_code == 201
    node_id = created.json()["item"]["id"]

    updated = group_ops_api_client.put(
        f"/api/admin/automation-conversion/group-ops/plans/1/nodes/{node_id}",
        json={
            "day_index": 4,
            "scheduled_time": "10:00",
            "action_title": "历史附件动作",
            "content_package_json": {
                "content_text": "新话术",
                "image_library_ids": [12],
            },
            "sort_order": 70,
            "status": "active",
        },
    )

    assert updated.status_code == 200
    item = updated.json()["item"]
    assert item["content_package_json"]["content_text"] == "新话术"
    assert item["content_package_json"]["image_library_ids"] == [12]
    assert item["attachments"] == []


def test_standard_plan_nodes_reject_invalid_scheduled_time(group_ops_api_client):
    for scheduled_time in ["20:15", "07:30", "24:00"]:
        response = group_ops_api_client.post(
            "/api/admin/automation-conversion/group-ops/plans/1/nodes",
            json={
                "day_index": 1,
                "scheduled_time": scheduled_time,
                "action_title": f"非法时间 {scheduled_time}",
                "text_content": "非法时间测试",
                "attachments": [],
                "sort_order": 10,
                "status": "active",
            },
        )
        assert response.status_code == 400


def test_standard_plan_nodes_accept_boundary_scheduled_time(group_ops_api_client):
    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/nodes",
        json={
            "day_index": 1,
            "scheduled_time": "23:30",
            "action_title": "晚间提醒",
            "text_content": "23:30 提醒",
            "attachments": [],
            "sort_order": 50,
            "status": "active",
        },
    )

    assert response.status_code == 201
    assert response.json()["item"]["scheduled_time"] == "23:30"


def test_standard_plan_nodes_derive_scheduled_time_from_legacy_trigger_time_label(group_ops_api_client):
    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans/1/nodes",
        json={
            "day_index": 4,
            "trigger_time_label": "第 4 天 20:30",
            "action_title": "旧字段兼容",
            "text_content": "旧字段兼容正文",
            "attachments": [],
            "sort_order": 60,
            "status": "active",
        },
    )

    assert response.status_code == 201
    assert response.json()["item"]["scheduled_time"] == "20:30"


def test_webhook_config_returns_no_plaintext_token_or_examples(group_ops_api_client):
    response = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans/2/webhook")

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "POST"
    assert body["webhook_url"].endswith("/api/automation/group-ops/webhooks/daily-lesson-8f3a")
    assert body["auth_mode"] == "aicrm_hmac_sha256"
    forbidden = {
        "token",
        "secret",
        "token_plaintext",
        "webhook_token",
        "request_example",
        "json_example",
        "usage",
        "description",
    }
    assert not (forbidden & set(body))


def test_webhook_shared_secret_rotation_route_is_removed(group_ops_api_client):
    removed = group_ops_api_client.post("/api/admin/automation-conversion/group-ops/plans/2/webhook/regenerate")
    config = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/plans/2/webhook")

    assert removed.status_code == 404
    assert config.status_code == 200
    assert "plaintext_token" not in config.json()
    assert "token_status" not in config.json()
