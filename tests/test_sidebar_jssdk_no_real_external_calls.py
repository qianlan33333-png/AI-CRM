from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

import aicrm_next.crm.identity_contact.sidebar_jssdk as api
import aicrm_next.channels.integration_gateway.wecom_jssdk_adapter as adapter
from aicrm_next.main import create_app


def test_sidebar_jssdk_sources_do_not_reintroduce_legacy_fallback() -> None:
    sources = "\n".join(
        [
            inspect.getsource(api.sidebar_jssdk_config),
            inspect.getsource(adapter.build_sidebar_jssdk_config),
            inspect.getsource(adapter.normalize_jssdk_url),
        ]
    )

    for marker in [
        "forward_to_legacy_flask",
        "legacy_flask_facade",
        "X-AICRM-Compatibility-Facade",
        "requests.",
        "httpx.",
        "client.get(",
        "client.post(",
    ]:
        assert marker not in sources


def test_jssdk_ignores_real_enabled_without_explicit_gate(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-jssdk-no-real")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:1/aicrm_probe")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_SIDEBAR_JSSDK_ADAPTER_MODE", "real_enabled")
    monkeypatch.setenv("AICRM_SIDEBAR_JSSDK_ALLOWED_HOSTS", "example.com")
    monkeypatch.delenv("AICRM_SIDEBAR_JSSDK_REAL_ENABLED", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/sidebar/jssdk-config", params={"url": "https://example.com/sidebar/bind-mobile"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["adapter_mode"] == "real_blocked"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert "X-AICRM-Compatibility-Facade" not in response.headers
