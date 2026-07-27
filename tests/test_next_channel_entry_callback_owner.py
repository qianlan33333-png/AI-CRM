from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.platform.shared.runtime import runtime_route_map_state


def test_callback_routes_are_next_owner_not_legacy_facade(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    client = TestClient(create_app(), raise_server_exceptions=False)

    paths = {route.path: route.endpoint.__module__ for route in client.app.routes if getattr(route, "path", "") in {"/wecom/external-contact/callback", "/api/wecom/events"}}

    assert paths["/wecom/external-contact/callback"] == "aicrm_next.channels.channel_entry.api"
    assert paths["/api/wecom/events"] == "aicrm_next.channels.channel_entry.api"


def test_runtime_route_map_shows_next_callback_owner(monkeypatch):
    monkeypatch.delenv("AICRM_ALLOW_LEGACY_WECOM_CALLBACK_FALLBACK", raising=False)

    state = runtime_route_map_state()

    assert state["route_owner"] == "ai_crm_next"
    assert state["next_live_callback_gateway_enabled"] is True
    assert state["callback_async_enabled"] == "next_task_queue"
    assert state["legacy_callback_fallback_enabled"] is False
    assert state["wecom_callback_routes"]["/wecom/external-contact/callback"] == "aicrm_next.channels.channel_entry.api"

