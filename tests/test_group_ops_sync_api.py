from __future__ import annotations

from tests.group_ops_test_helpers import group_ops_api_client


def test_unsynced_groups_leave_new_plan_group_choices_empty(group_ops_api_client):
    from aicrm_next.automation.automation_engine.group_ops.repo import reset_group_ops_fixture_state

    reset_group_ops_fixture_state(seed_groups=False)
    created = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={"plan_name": "未同步群计划", "plan_type": "standard", "owner_userid": "owner_001", "status": "draft"},
    )
    plan_id = created.json()["item"]["id"]

    groups = group_ops_api_client.get(f"/api/admin/automation-conversion/group-ops/plans/{plan_id}/groups")
    available = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/groups?owner_userid=owner_001")

    assert created.status_code == 201
    assert groups.status_code == 200
    assert groups.json()["items"] == []
    assert available.status_code == 200
    assert available.json()["items"] == []


def test_group_sync_preview_does_not_write_snapshots(group_ops_api_client, monkeypatch):
    from aicrm_next.automation.automation_engine.group_ops.repo import reset_group_ops_fixture_state

    reset_group_ops_fixture_state(seed_groups=False)
    monkeypatch.setenv("AICRM_WECOM_GROUP_ADAPTER_MODE", "fake")

    preview = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/groups/sync/preview",
        json={"owner_userid": "owner_001", "limit": 10, "operator": "pytest"},
    )
    after_preview = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/groups?owner_userid=owner_001")

    assert preview.status_code == 200
    body = preview.json()
    assert len(body["items"]) >= 2
    assert body["total"] >= 2
    assert body["side_effect_safety"]["no_db_write"] is True
    assert body["side_effect_safety"]["no_outbound_send"] is True
    assert after_preview.status_code == 200
    assert after_preview.json()["items"] == []


def test_group_sync_writes_snapshots_and_reports_create_update(group_ops_api_client, monkeypatch):
    from aicrm_next.automation.automation_engine.group_ops.repo import reset_group_ops_fixture_state

    reset_group_ops_fixture_state(seed_groups=False)
    monkeypatch.setenv("AICRM_WECOM_GROUP_ADAPTER_MODE", "fake")

    synced = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/groups/sync",
        json={"owner_userid": "owner_001", "limit": 10, "operator": "pytest"},
    )
    synced_again = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/groups/sync",
        json={"owner_userid": "owner_001", "limit": 10, "operator": "pytest"},
    )
    owner_001 = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/groups?owner_userid=owner_001")
    admin_001 = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/groups?owner_userid=admin_001")

    assert synced.status_code == 200
    assert synced.json()["synced_count"] >= 2
    assert synced.json()["new_count"] >= 2
    assert synced_again.status_code == 200
    assert synced_again.json()["updated_count"] >= 2
    assert {item["owner_userid"] for item in owner_001.json()["items"]} == {"owner_001"}
    assert admin_001.status_code == 200
    assert admin_001.json()["items"] == []


def test_group_sync_owner_filter_keeps_owners_separate(group_ops_api_client, monkeypatch):
    from aicrm_next.automation.automation_engine.group_ops.repo import reset_group_ops_fixture_state

    reset_group_ops_fixture_state(seed_groups=False)
    monkeypatch.setenv("AICRM_WECOM_GROUP_ADAPTER_MODE", "fake")

    group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/groups/sync",
        json={"owner_userid": "owner_001", "limit": 10, "operator": "pytest"},
    )
    group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/groups/sync",
        json={"owner_userid": "owner_002", "limit": 10, "operator": "pytest"},
    )
    owner_001 = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/groups?owner_userid=owner_001")
    owner_002 = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/groups?owner_userid=owner_002")
    admin_001 = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/groups?owner_userid=admin_001")

    assert {item["owner_userid"] for item in owner_001.json()["items"]} == {"owner_001"}
    assert {item["owner_userid"] for item in owner_002.json()["items"]} == {"owner_002"}
    assert len(owner_001.json()["items"]) >= 2
    assert len(owner_002.json()["items"]) >= 1
    assert [item["chat_id"] for item in admin_001.json()["items"]] == ["wrOgBBB001"]
    assert admin_001.json()["items"][0]["admin_userids"] == ["admin_001"]


def test_group_sync_refreshes_stale_admin_candidate_details():
    from aicrm_next.automation.automation_engine.group_ops.application import SyncGroupOpsGroupsCommand
    from aicrm_next.automation.automation_engine.group_ops.dto import GroupOpsGroupSyncRequest
    from aicrm_next.automation.automation_engine.group_ops.repo import InMemoryGroupOpsRepository

    class RefreshingAdapter:
        def __init__(self):
            self.refreshed_chat_ids = []

        def list_group_chats(self, *, owner_userid: str, limit: int = 100, cursor: str = ""):
            return {
                "ok": True,
                "mode": "fake",
                "groups": [],
                "next_cursor": "",
                "warnings": [],
                "skipped_count": 0,
            }

        def get_group_chat(self, *, chat_id: str, need_name: int = 1, owner_userid: str = ""):
            self.refreshed_chat_ids.append(chat_id)
            if chat_id != "wrOgSTALE001":
                return {"ok": False, "group": {}, "error_message": "not found"}
            return {
                "ok": True,
                "group": {
                    "chat_id": "wrOgSTALE001",
                    "group_name": "旧缓存管理员群",
                    "owner_userid": "owner_002",
                    "owner_name": "李小红",
                    "admin_userids": ["admin_001"],
                    "internal_member_count": 8,
                    "external_member_count": 88,
                    "status": "active",
                },
            }

    repo = InMemoryGroupOpsRepository(seed_groups=False)
    repo.upsert_group_asset(
        {
            "chat_id": "wrOgSTALE001",
            "group_name": "旧缓存管理员群",
            "owner_userid": "owner_002",
            "owner_name": "李小红",
            "admin_userids": [],
            "internal_member_count": 8,
            "external_member_count": 88,
            "status": "active",
        }
    )
    adapter = RefreshingAdapter()

    response = SyncGroupOpsGroupsCommand(repo=repo, sync_adapter=adapter)(
        GroupOpsGroupSyncRequest(owner_userid="admin_001", limit=10, operator="pytest")
    )
    groups, total = repo.list_group_assets({"owner_userid": "admin_001", "limit": 10})

    assert response["status"] == "synced"
    assert response["synced_count"] == 1
    assert "wrOgSTALE001" in adapter.refreshed_chat_ids
    assert [item["chat_id"] for item in response["items"]] == ["wrOgSTALE001"]
    assert [item["chat_id"] for item in groups] == ["wrOgSTALE001"]
    assert total == 1
    assert response["items"][0]["admin_userids"] == ["admin_001"]
    assert "included_admin_groups_from_refreshed_candidates=1" in response["warnings"]


def test_group_sync_recovers_owned_group_when_legacy_candidate_owner_is_stale():
    from aicrm_next.automation.automation_engine.group_ops.application import SyncGroupOpsGroupsCommand
    from aicrm_next.automation.automation_engine.group_ops.dto import GroupOpsGroupSyncRequest
    from aicrm_next.automation.automation_engine.group_ops.repo import InMemoryGroupOpsRepository

    class CurrentOwnerAdapter:
        def list_group_chats(self, *, owner_userid: str, limit: int = 100, cursor: str = ""):
            return {"ok": True, "mode": "production", "groups": [], "next_cursor": "", "warnings": [], "skipped_count": 0}

        def get_group_chat(self, *, chat_id: str, need_name: int = 1, owner_userid: str = ""):
            assert chat_id == "wrbNXyCwAAm0Vx7_OVQ_-PkT6Exeg8pg"
            return {
                "ok": True,
                "group": {
                    "chat_id": chat_id,
                    "group_name": "老黄的AI+进化同行圈",
                    "owner_userid": "HuangYouCan",
                    "owner_name": "HuangYouCan",
                    "admin_userids": ["ShangZiYi", "ZhaoYanFang"],
                    "internal_member_count": 5,
                    "external_member_count": 110,
                    "status": "active",
                },
            }

    repo = InMemoryGroupOpsRepository(seed_groups=False)
    repo.upsert_group_asset(
        {
            "chat_id": "wrbNXyCwAAm0Vx7_OVQ_-PkT6Exeg8pg",
            "group_name": "旧缓存群名",
            "owner_userid": "QianLan",
            "owner_name": "QianLan",
            "admin_userids": ["HuangYouCan"],
            "status": "active",
        }
    )
    assert repo.list_admin_group_assets("HuangYouCan")[0]["group_name"] == "旧缓存群名"

    response = SyncGroupOpsGroupsCommand(repo=repo, sync_adapter=CurrentOwnerAdapter())(
        GroupOpsGroupSyncRequest(owner_userid="HuangYouCan", limit=10, operator="pytest")
    )

    assert response["synced_count"] == 1
    assert response["items"][0]["group_name"] == "老黄的AI+进化同行圈"
    assert response["items"][0]["owner_userid"] == "HuangYouCan"
    assert response["items"][0]["internal_member_count"] + response["items"][0]["external_member_count"] == 115
    assert "included_admin_groups_from_refreshed_candidates=1" in response["warnings"]


def test_group_sync_prefers_current_wecom_group_over_stale_admin_cache():
    from aicrm_next.automation.automation_engine.group_ops.application import SyncGroupOpsGroupsCommand
    from aicrm_next.automation.automation_engine.group_ops.dto import GroupOpsGroupSyncRequest
    from aicrm_next.automation.automation_engine.group_ops.repo import InMemoryGroupOpsRepository

    chat_id = "wrbNXyCwAAm0Vx7_OVQ_-PkT6Exeg8pg"

    class CurrentListAdapter:
        def list_group_chats(self, *, owner_userid: str, limit: int = 100, cursor: str = ""):
            return {
                "ok": True,
                "mode": "production",
                "groups": [
                    {
                        "chat_id": chat_id,
                        "group_name": "老黄的AI+进化同行圈",
                        "owner_userid": "HuangYouCan",
                        "owner_name": "HuangYouCan",
                        "admin_userids": ["ShangZiYi", "ZhaoYanFang"],
                        "internal_member_count": 5,
                        "external_member_count": 110,
                        "status": "active",
                    }
                ],
                "next_cursor": "",
                "warnings": [],
                "skipped_count": 0,
            }

        def get_group_chat(self, *, chat_id: str, need_name: int = 1, owner_userid: str = ""):
            raise AssertionError("a group already returned by WeCom list must not need candidate refresh")

    repo = InMemoryGroupOpsRepository(seed_groups=False)
    repo.upsert_group_asset(
        {
            "chat_id": chat_id,
            "group_name": "旧缓存群名",
            "owner_userid": "QianLan",
            "owner_name": "QianLan",
            "admin_userids": ["HuangYouCan"],
            "internal_member_count": 1,
            "external_member_count": 1,
            "status": "active",
        }
    )

    response = SyncGroupOpsGroupsCommand(repo=repo, sync_adapter=CurrentListAdapter())(
        GroupOpsGroupSyncRequest(owner_userid="HuangYouCan", limit=200, operator="pytest")
    )

    assert response["synced_count"] == 1
    assert response["items"][0]["chat_id"] == chat_id
    assert response["items"][0]["group_name"] == "老黄的AI+进化同行圈"
    assert response["items"][0]["owner_userid"] == "HuangYouCan"
    assert response["items"][0]["internal_member_count"] + response["items"][0]["external_member_count"] == 115


def test_group_sync_binding_does_not_prevalidate_cached_owner(group_ops_api_client, monkeypatch):
    from aicrm_next.automation.automation_engine.group_ops.repo import reset_group_ops_fixture_state

    reset_group_ops_fixture_state(seed_groups=False)
    monkeypatch.setenv("AICRM_WECOM_GROUP_ADAPTER_MODE", "fake")

    created = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/plans",
        json={"plan_name": "owner 001 计划", "plan_type": "standard", "owner_userid": "owner_001", "status": "draft"},
    )
    group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/groups/sync",
        json={"owner_userid": "owner_002", "limit": 10, "operator": "pytest"},
    )
    groups = group_ops_api_client.get("/api/admin/automation-conversion/group-ops/groups?owner_userid=owner_002")
    bad = group_ops_api_client.post(
        f"/api/admin/automation-conversion/group-ops/plans/{created.json()['item']['id']}/groups",
        json={"chat_id": groups.json()["items"][0]["chat_id"]},
    )

    assert bad.status_code == 201
    assert bad.json()["item"]["chat_id"] == groups.json()["items"][0]["chat_id"]


def test_group_sync_default_disabled_blocks_without_real_wecom(group_ops_api_client, monkeypatch):
    monkeypatch.delenv("AICRM_WECOM_GROUP_ADAPTER_MODE", raising=False)

    response = group_ops_api_client.post(
        "/api/admin/automation-conversion/group-ops/groups/sync",
        json={"owner_userid": "owner_001", "limit": 10, "operator": "pytest"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["ok"] is False
    assert body["status"] in {"blocked", "disabled"}
    assert body["synced_count"] == 0


def test_group_sync_fake_adapter_data_is_stable():
    from aicrm_next.channels.integration_gateway.wecom_group_adapter import WeComGroupAssetAdapter

    owner_001 = WeComGroupAssetAdapter(mode="fake").list_group_chats(owner_userid="owner_001", limit=100)
    owner_002 = WeComGroupAssetAdapter(mode="fake").list_group_chats(owner_userid="owner_002", limit=100)

    assert owner_001["ok"] is True
    assert owner_002["ok"] is True
    assert owner_001["side_effect_executed"] is False
    assert owner_002["side_effect_executed"] is False
    assert len(owner_001["groups"]) >= 2
    assert len(owner_002["groups"]) >= 1
