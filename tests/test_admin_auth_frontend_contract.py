from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next/app/admin_console/templates/admin_console/login.html"


def test_login_template_exposes_only_wecom_links() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "api.admin_login" not in source
    assert "login_type" not in source
    assert "break_glass" not in source
    assert "login_links.qr" in source
    assert "login_links.oauth" in source
    assert "forward_to_legacy_flask" not in source


def test_rendered_login_points_to_wecom_and_has_no_password_form(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "admin-auth-frontend-contract-test")
    response = TestClient(create_app(), raise_server_exceptions=False).get("/login")

    assert response.status_code == 200
    assert 'method="post" action="/login"' not in response.text
    assert "/auth/wecom/start?mode=qr" in response.text
    assert "不提供本地账密入口" in response.text
