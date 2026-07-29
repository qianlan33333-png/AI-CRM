from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.hxc.hxc_dashboard.safe_mode import reset_hxc_safe_mode_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "hxc-dashboard-send-config-test")
    reset_hxc_safe_mode_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def test_send_config_read_contract(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/admin/hxc-dashboard/send-config")

    assert response.status_code == 200
    body = response.json()
    assert body["source_status"] == "next_hxc_send_config"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["send_configs"][0]["sender_userid"] == "hxc_sender_fixture"
    assert body["directory_candidates"]


def test_send_config_upsert_validation_and_local_state(monkeypatch) -> None:
    client = _client(monkeypatch)

    invalid = client.post("/api/admin/hxc-dashboard/send-config", json={"display_name": "Missing"})
    saved = client.post(
        "/api/admin/hxc-dashboard/send-config",
        json={"sender_userid": "hxc_sender_new", "display_name": "New Sender", "priority": 9, "is_active": True},
    )
    listing = client.get("/api/admin/hxc-dashboard/send-config")

    assert invalid.status_code == 400
    assert invalid.json()["fallback_used"] is False
    assert "sender_userid" in invalid.json()["error"]
    assert saved.status_code == 200
    assert saved.json()["source_status"] == "next_hxc_send_config_command"
    assert saved.json()["send_config"]["sender_userid"] == "hxc_sender_new"
    assert any(item["sender_userid"] == "hxc_sender_new" for item in listing.json()["send_configs"])


def test_send_config_delete_is_local_and_idempotent(monkeypatch) -> None:
    client = _client(monkeypatch)

    client.post("/api/admin/hxc-dashboard/send-config", json={"sender_userid": "hxc_sender_delete", "display_name": "Delete Me"})
    deleted = client.delete("/api/admin/hxc-dashboard/send-config/hxc_sender_delete")
    second = client.delete("/api/admin/hxc-dashboard/send-config/hxc_sender_delete")

    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert deleted.json()["fallback_used"] is False
    assert deleted.json()["real_external_call_executed"] is False
    assert second.status_code == 200
    assert second.json()["status"] == "not_found"
    assert second.json()["skipped_count"] == 1
