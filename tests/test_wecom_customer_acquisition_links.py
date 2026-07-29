from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from aicrm_next.automation.automation_engine.channels_api import reset_wecom_customer_acquisition_link_fixture_state
from aicrm_next.main import create_app


def make_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "next-wecom-customer-acquisition-test")
    reset_wecom_customer_acquisition_link_fixture_state()
    return TestClient(create_app())


def assert_next_contract(payload: dict, *, ok: bool = True) -> None:
    assert payload["ok"] is ok
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False


def test_customer_acquisition_links_create_final_url_without_real_wecom_call(monkeypatch) -> None:
    client = make_client(monkeypatch)

    created = client.post(
        "/api/admin/wecom-customer-acquisition-links",
        json={
            "link_id": "link-create-001",
            "link_name": "June Acquisition",
            "link_url": "https://work.weixin.qq.com/ca/test-link?foo=1#frag",
            "program_id": 1,
            "workflow_id": 2,
            "initial_audience_code": "operating",
        },
        headers={"Idempotency-Key": "wca-create-1"},
    ).json()

    assert_next_contract(created)
    assert created["source_status"] == "next_command"
    assert created["idempotency_key"] == "wca-create-1"
    assert created["adapter_mode"] == "real_blocked"
    assert created["wecom_api_called"] is False
    assert created["side_effect_plan"]["status"] == "blocked"
    link = created["link"]
    parsed = urlsplit(link["final_url"])
    assert parsed.fragment == "frag"
    assert parse_qs(parsed.query)["customer_channel"] == [link["customer_channel"]]
    assert parse_qs(parsed.query)["foo"] == ["1"]
    assert "program_id" not in link
    assert "workflow_id" not in link
    assert "initial_audience_code" not in link


def test_customer_acquisition_links_list_update_delete_and_actions_are_safe_mode(monkeypatch) -> None:
    client = make_client(monkeypatch)
    created = client.post("/api/admin/wecom-customer-acquisition-links", json={"link_id": "link-safe-001"}).json()["link"]

    listed = client.get("/api/admin/wecom-customer-acquisition-links").json()
    assert_next_contract(listed)
    assert listed["adapter_mode"] == "real_blocked"
    assert listed["wecom_api_called"] is False
    assert created["id"] in {item["id"] for item in listed["items"]}

    patched = client.patch(
        f"/api/admin/wecom-customer-acquisition-links/{created['id']}",
        json={"description": "updated", "initial_audience_code": "operating"},
    ).json()
    assert "initial_audience_code" not in patched["link"]
    assert "program_id" not in patched["link"]
    assert "workflow_id" not in patched["link"]
    deleted = client.delete(f"/api/admin/wecom-customer-acquisition-links/{created['id']}").json()
    enabled = client.post(f"/api/admin/wecom-customer-acquisition-links/{created['id']}/enable").json()
    synced = client.post(f"/api/admin/wecom-customer-acquisition-links/{created['id']}/sync").json()

    for payload in [patched, deleted, enabled, synced]:
        assert_next_contract(payload)
        assert payload["adapter_mode"] == "real_blocked"
        assert payload["wecom_api_called"] is False
        assert payload["side_effect_plan"]["status"] == "blocked"

    deprecated = client.post(f"/api/admin/wecom-customer-acquisition-links/{created['id']}/refresh").json()
    assert_next_contract(deprecated, ok=False)
    assert deprecated["error_code"] == "wecom_customer_acquisition_action_deprecated"
