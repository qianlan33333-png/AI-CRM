from __future__ import annotations

from fastapi.testclient import TestClient


def test_auth_wecom_start_and_callback_are_exact_next_blocked_routes(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    from aicrm_next.main import create_app

    client = TestClient(create_app())

    for path, auth_step in [
        ("/auth/wecom/start?mode=qr&next=/admin/config", "wecom_sso_start"),
        ("/auth/wecom/callback?code=mock-code&state=state", "wecom_sso_callback"),
    ]:
        response = client.get(path)
        payload = response.json()

        assert response.status_code == 503
        assert response.headers.get("X-AICRM-Compatibility-Facade") is None
        assert payload["error_code"] == "external_call_blocked"
        assert payload["auth_step"] == auth_step
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["source_status"] == "external_call_blocked"
        assert payload["adapter_mode"] == "real_blocked"
        assert payload["fallback_used"] is False
        assert payload["real_external_call_executed"] is False
        assert payload["side_effect_plan"]["real_external_call_executed"] is False


def test_auth_wecom_exact_options_return_next_owner_diagnostics() -> None:
    from aicrm_next.main import create_app

    client = TestClient(create_app())

    for path in ["/auth/wecom/start", "/auth/wecom/callback"]:
        response = client.options(path)
        payload = response.json()

        assert response.status_code == 200
        assert payload["source_status"] == "next_auth_wecom_exact"
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["fallback_used"] is False
        assert payload["real_external_call_executed"] is False


def test_auth_wecom_exact_routes_are_registered_before_production_compat_wildcard(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    from aicrm_next.main import create_app

    app = create_app()
    by_path = {getattr(route, "path", ""): route for route in app.routes}

    for path in ["/auth/wecom/start", "/auth/wecom/callback"]:
        endpoint = getattr(by_path[path], "endpoint", None)
        assert getattr(endpoint, "__module__", "") == "aicrm_next.channels.auth_wecom.api"
