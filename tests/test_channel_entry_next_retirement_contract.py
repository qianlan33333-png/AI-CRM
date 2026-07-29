from __future__ import annotations

from aicrm_next.main import create_app
from aicrm_next.platform.shared.runtime import runtime_route_map_state


CHANNEL_ENTRY_ROUTES = {
    "/wecom/external-contact/callback": "aicrm_next.channels.channel_entry.api",
    "/api/wecom/events": "aicrm_next.channels.channel_entry.api",
}


def test_channel_entry_callbacks_are_next_owned_with_legacy_fallback_disabled(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://route:route@127.0.0.1:1/aicrm_route")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_ALLOW_LEGACY_WECOM_CALLBACK_FALLBACK", "1")

    app = create_app()
    route_modules = {
        route.path: route.endpoint.__module__
        for route in app.routes
        if getattr(route, "path", "") in CHANNEL_ENTRY_ROUTES
    }

    assert route_modules == CHANNEL_ENTRY_ROUTES
    assert runtime_route_map_state()["legacy_callback_fallback_enabled"] is False


def test_wecom_callback_facade_no_longer_exports_legacy_runtime_handler():
    from aicrm_next.channels.integration_gateway import wecom_callback_facade

    assert not hasattr(wecom_callback_facade, "handle_wecom_callback_via_legacy")
