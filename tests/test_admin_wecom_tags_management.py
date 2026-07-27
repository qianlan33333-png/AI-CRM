from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from aicrm_next.crm.customer_tags.admin_write import reset_wecom_tag_write_fixture_state
from aicrm_next.crm.customer_tags.sync_service import execute_wecom_tag_catalog_sync
from aicrm_next.main import create_app


class FakeSyncRepository:
    source_status = "fake_projection"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def refresh_catalog(self, *, groups, tags, synced_at, operator, raw_response):
        self.calls.append(
            {
                "groups": groups,
                "tags": tags,
                "synced_at": synced_at,
                "operator": operator,
                "raw_response": raw_response,
            }
        )
        return {"sync_run_id": 11, "upserted_groups": len(groups), "upserted_tags": len(tags), "marked_deleted_tags": 0}


class FakeGateway:
    def __init__(self) -> None:
        self.catalog = {
            "errcode": 0,
            "tag_group": [
                {
                    "group_id": "group_stage",
                    "group_name": "客户阶段",
                    "tag": [
                        {"id": "tag_new", "name": "新线索", "order": 1},
                        {"tag_id": "tag_trial", "tag_name": "体验中", "order": 2},
                    ],
                }
            ],
        }

    def list_wecom_tags_live(self):
        return deepcopy(self.catalog)


def make_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "next-wecom-tags-test")
    reset_wecom_tag_write_fixture_state()
    return TestClient(create_app())


def assert_next_contract(payload: dict) -> None:
    assert payload["ok"] is True
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False


def test_wecom_tag_routes_use_next_read_and_write_contract(monkeypatch) -> None:
    client = make_client(monkeypatch)

    listing = client.get("/api/admin/wecom/tags").json()
    assert_next_contract(listing)
    assert listing["source_status"] == "local_contract_probe"
    assert listing["tag_limit"] == 1000
    assert listing["total_tags"] == len(listing["items"])
    assert listing["groups"][0]["tags"][0].keys() >= {"tag_id", "tag_name", "group_id", "group_name"}

    created_group = client.post("/api/admin/wecom/tag-groups", json={"group_name": "新阶段", "first_tag_name": "高意向"}).json()
    created_tag = client.post("/api/admin/wecom/tags", json={"group_id": "group_fixture_lifecycle", "tag_name": "复购"}).json()
    updated_group = client.put("/api/admin/wecom/tag-groups/group_fixture_lifecycle", json={"group_name": "阶段更新"}).json()
    updated_tag = client.put("/api/admin/wecom/tags/tag_fixture_active", json={"tag_name": "活跃更新"}).json()
    deleted_tag = client.delete("/api/admin/wecom/tags/tag_fixture_trial").json()

    for payload in [created_group, created_tag, updated_group, updated_tag, deleted_tag]:
        assert_next_contract(payload)
        assert payload["source_status"] == "next_command"
        assert payload["write_model_status"] == "local_projection_updated"
        assert payload["side_effect_plan"]["adapter_mode"] == "real_blocked"


def test_wecom_tag_sync_uses_fake_gateway_without_real_wecom_call() -> None:
    repo = FakeSyncRepository()
    gateway = FakeGateway()

    payload = execute_wecom_tag_catalog_sync(operator="test-admin", gateway=gateway, repository=repo)

    assert payload["ok"] is True
    assert payload["source_status"] == "next_live_remote_synced"
    assert payload["sync_model_status"] == "fake_projection"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["fetched_groups"] == 1
    assert payload["fetched_tags"] == 2
    assert payload["upserted_tags"] == 2
    assert repo.calls[0]["operator"] == "test-admin"
    assert repo.calls[0]["tags"][0]["tag_id"] == "tag_new"
    assert repo.calls[0]["tags"][1]["tag_id"] == "tag_trial"
