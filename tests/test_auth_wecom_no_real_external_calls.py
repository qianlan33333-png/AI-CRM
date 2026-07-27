from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_auth_wecom_exact_routes_do_not_execute_real_oauth_or_wecom(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ENABLE_REAL", raising=False)
    from aicrm_next.main import create_app

    client = TestClient(create_app())

    for path in ["/auth/wecom/start", "/auth/wecom/callback", "/auth/wecom/unknown"]:
        response = client.get(path)
        payload = response.json()

        assert response.status_code in {410, 503}
        assert payload["adapter_mode"] == "real_blocked"
        assert payload["fallback_used"] is False
        assert payload["real_external_call_executed"] is False
        assert "access_token" not in str(payload)
        assert "app_secret" not in str(payload)


def test_auth_wecom_next_module_has_no_external_call_clients_or_token_leak_markers() -> None:
    source = Path("aicrm_next/channels/auth_wecom/api.py").read_text(encoding="utf-8")

    forbidden = [
        "forward_to_legacy_flask",
        "legacy_flask_facade",
        "requests.post(",
        "httpx.post(",
        "exchange_code_for_wecom_user",
        "build_wecom_qr_login_url",
        "build_wecom_oauth_login_url",
        '"real_external_call_executed": True',
        "'real_external_call_executed': True",
        "access_token\":",
        "app_secret\":",
    ]
    for marker in forbidden:
        assert marker not in source
