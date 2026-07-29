from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


def test_runtime_route_map_exposes_next_channel_entry_owner() -> None:
    response = TestClient(create_app()).get("/api/system/runtime-route-map")

    assert response.status_code == 200
    payload = response.json()
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["wecom_callback_routes"]["/wecom/external-contact/callback"] == "aicrm_next.channels.channel_entry.api"
    assert payload["legacy_callback_fallback_enabled"] is False
    assert "web_release_sha" in payload
