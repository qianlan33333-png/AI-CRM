from __future__ import annotations

from fastapi.testclient import TestClient

import aicrm_next.crm.customer_tags.api as api
from aicrm_next.crm.customer_tags.sync_service import execute_wecom_tag_catalog_sync
from aicrm_next.integration_gateway.wecom_tag_live_gateway import WeComTagLiveGateway
from aicrm_next.main import create_app
from aicrm_next.shared.database import get_sqlalchemy_database_url


class FakeSyncRepository:
    source_status = "test_projection"

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
        return {"sync_run_id": 7, "upserted_groups": len(groups), "upserted_tags": len(tags), "marked_deleted_tags": 1}


class FakeGateway:
    def list_wecom_tags_live(self):
        return {
            "errcode": 0,
            "tag_group": [
                {
                    "group_id": "group_a",
                    "group_name": "客户阶段",
                    "tag": [
                        {"id": "tag_a", "name": "高意向", "order": 3},
                        {"tag_id": "tag_b", "tag_name": "体验中"},
                    ],
                }
            ],
        }


def test_wecom_tag_sync_uses_next_live_gateway_and_projection_repository(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod_user:prod_pass@db.internal:5432/prod_crm")
    repo = FakeSyncRepository()

    payload = execute_wecom_tag_catalog_sync(operator="admin", gateway=FakeGateway(), repository=repo)

    assert payload["ok"] is True
    assert payload["source_status"] == "next_live_remote_synced"
    assert payload["sync_model_status"] == "test_projection"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is True
    assert payload["sync_executed"] is True
    assert payload["fetched_groups"] == 1
    assert payload["fetched_tags"] == 2
    assert payload["upserted_tags"] == 2
    assert repo.calls[0]["operator"] == "admin"
    assert repo.calls[0]["groups"][0]["group_id"] == "group_a"
    assert repo.calls[0]["tags"][0]["tag_id"] == "tag_a"
    assert repo.calls[0]["tags"][1]["tag_id"] == "tag_b"


def test_wecom_tag_sync_route_allows_production_data_mode_without_write_model_block(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "wecom-tag-next-sync-route")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod_user:prod_pass@db.internal:5432/prod_crm")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")

    def fake_sync(*, operator: str = ""):
        return {
            "ok": True,
            "source_status": "next_live_remote_synced",
            "sync_model_status": "test_projection",
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": True,
            "sync_executed": True,
            "operator": operator,
        }

    monkeypatch.setattr(api, "execute_wecom_tag_catalog_sync", fake_sync)
    response = TestClient(create_app(), raise_server_exceptions=False).post(
        "/api/admin/wecom/tags/sync",
        json={"actor_id": "admin_user"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["source_status"] == "next_live_remote_synced"
    assert payload["operator"] == "admin_user"
    assert payload["sync_executed"] is True
    assert "write model is not production-ready" not in response.text


def test_fake_stub_routes_are_not_registered_in_runtime(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = TestClient(create_app(), raise_server_exceptions=False).get("/api/admin/wecom/tags/fake-stub")

    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"


def test_wecom_tag_gateway_accepts_existing_wecom_contact_env_names(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"errcode":0,"access_token":"token"}'

        return Response()

    monkeypatch.delenv("AICRM_WECOM_TAG_CORP_ID", raising=False)
    monkeypatch.delenv("AICRM_WECOM_TAG_AGENT_SECRET", raising=False)
    monkeypatch.setenv("WECOM_CORP_ID", "ww_existing")
    monkeypatch.setenv("WECOM_CONTACT_SECRET", "contact_secret")
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_tag_live_gateway.urlopen", fake_urlopen)

    assert WeComTagLiveGateway(api_base="https://qy.example.test")._access_token() == "token"
    assert "corpid=ww_existing" in captured["url"]
    assert "corpsecret=contact_secret" in captured["url"]


def test_wecom_tag_gateway_supports_live_corp_tag_crud_client_contract() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def add_corp_tag(self, payload):
            self.calls.append({"operation": "add", "payload": payload})
            return {"errcode": 0, "tag_group": {"group_id": payload["group_id"], "tag": [{"id": "etb_tag", "name": payload["tag"][0]["name"]}]}}

        def edit_corp_tag(self, payload):
            self.calls.append({"operation": "edit", "payload": payload})
            return {"errcode": 0}

        def del_corp_tag(self, payload):
            self.calls.append({"operation": "delete", "payload": payload})
            return {"errcode": 0}

    fake = FakeClient()
    gateway = WeComTagLiveGateway(client=fake)

    add = gateway.add_corp_tag_live(group_id="group_a", tags=[{"name": "同行圈体验版"}])
    edit = gateway.edit_corp_tag_live(tag_or_group_id="etb_tag", name="同行圈体验版更新")
    delete = gateway.delete_corp_tag_live(tag_ids=["etb_tag"])

    assert add["tag_group"]["tag"][0]["id"] == "etb_tag"
    assert edit["errcode"] == 0
    assert delete["errcode"] == 0
    assert fake.calls == [
        {"operation": "add", "payload": {"tag": [{"name": "同行圈体验版"}], "group_id": "group_a"}},
        {"operation": "edit", "payload": {"id": "etb_tag", "name": "同行圈体验版更新"}},
        {"operation": "delete", "payload": {"tag_id": ["etb_tag"], "group_id": []}},
    ]


def test_shared_database_url_prefers_runtime_database_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.local:5432/app")

    assert get_sqlalchemy_database_url() == "postgresql+psycopg://u:p@db.local:5432/app"
