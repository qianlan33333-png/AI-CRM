from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_admin_auth_routes_do_not_execute_real_external_calls(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "admin-auth-no-real-calls-test")
    client = TestClient(create_app(), raise_server_exceptions=False)

    for response in [client.options("/login"), client.options("/logout")]:
        assert response.status_code == 200
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert response.json()["fallback_used"] is False
        assert response.json()["real_external_call_executed"] is False
        assert response.json()["wecom_token_exchange_executed"] is False


def test_admin_auth_next_module_has_no_legacy_or_http_token_exchange_markers() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "aicrm_next/platform/admin_auth").glob("*.py"))
    forbidden = [
        "forward_to_" + "legacy_flask",
        "legacy_" + "flask_facade",
        "admin_" + "auth_routes",
        "requests.",
        "http" + "x",
        "exchange_code_for_" + "wecom_user",
    ]

    assert [marker for marker in forbidden if marker in source] == []
