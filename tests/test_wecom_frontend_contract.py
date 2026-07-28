from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_TEMPLATE = ROOT / "aicrm_next" / "automation" / "automation_engine" / "templates" / "admin_console" / "channel_code_center.html"


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "p1-wecom-frontend-contract-test")
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_channels_page_does_not_render_wecom_p1_diagnostics(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin/channels")
    html = response.text

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "渠道总数" in html
    assert "渠道码列表" in html
    assert "新建渠道" in html
    assert "weComP1StatusApp" not in html
    assert "weComP1StatusPayload" not in html
    assert "wecom_overview.js" not in html
    assert 'data-p1-diagnostics="wecom"' not in html
    assert "企微授权配置未完成，日常渠道配置不受阻塞。" not in html


def test_wecom_p1_rendered_fixture_does_not_expose_sensitive_strings(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin/channels")
    html = response.text

    for forbidden in [
        "raw_external_userid",
        "raw callback body",
        "raw_callback_body",
        "Authorization: Bearer",
        "access_token",
        "corpsecret",
        "suite_secret",
        "openid",
        "unionid",
        "13800138000",
    ]:
        assert forbidden not in html


def test_channel_template_keeps_channel_center_without_wecom_p1_diagnostics() -> None:
    template = CHANNEL_TEMPLATE.read_text(encoding="utf-8")

    assert "weComP1StatusApp" not in template
    assert "weComP1StatusPayload" not in template
    assert 'data-p1-diagnostics="wecom"' not in template
    assert "企微授权配置未完成，日常渠道配置不受阻塞。" not in template
    assert "admin_console/p1/wecom/wecom_overview.js" not in template
    assert 'data-channel-admission-page="channel-center"' in template
    assert "channel_code_center_next.js" in template
