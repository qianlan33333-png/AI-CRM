from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.admin_auth.service import admin_cookie_secure
from aicrm_next.main import create_app
from aicrm_next.shared.signed_session import session_cookie_secure
from scripts.ops.ensure_runtime_environment import ensure_runtime_environment, runtime_environment_values


def test_https_public_origin_enables_secure_cookies_and_hsts(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_ADMIN_SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("AICRM_PUBLIC_BASE_URL", "https://www.youcangogogo.com")

    response = TestClient(create_app()).get("/health")

    assert admin_cookie_secure() is True
    assert session_cookie_secure() is True
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_https_public_origin_cannot_be_downgraded_by_cookie_override(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_PUBLIC_BASE_URL", "https://www.youcangogogo.com")
    monkeypatch.setenv("AICRM_ADMIN_SESSION_COOKIE_SECURE", "0")

    assert admin_cookie_secure() is True
    assert session_cookie_secure() is True


def test_deploy_runtime_environment_persists_secure_non_secret_defaults(tmp_path: Path) -> None:
    environment_file = tmp_path / "runtime.env"
    environment_file.write_text(
        "EXISTING='kept'\n"
        "AICRM_NEXT_ENV='old'\n"
        "AICRM_QUESTIONNAIRE_EXTERNAL_PUSH_MODE='queue'\n"
        "AICRM_NEXT_WECOM_REAL_CALLS_ENABLED='true'\n"
        "AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE='1'\n"
        "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES='wecom.contact.tag.mark'\n"
        "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS='legacy-owner'\n",
        encoding="utf-8",
    )
    environment_file.chmod(0o600)

    values = ensure_runtime_environment(
        environment_file,
        target_environment="production",
        public_base_url="https://www.youcangogogo.com/",
        allow_missing_wechat_shop_callback_token=True,
    )
    body = environment_file.read_text(encoding="utf-8")

    assert values == runtime_environment_values(
        target_environment="production",
        public_base_url="https://www.youcangogogo.com/",
        allow_missing_wechat_shop_callback_token=True,
    )
    assert "EXISTING='kept'" in body
    assert "AICRM_NEXT_ENV='production'" in body
    assert "AICRM_ADMIN_SESSION_COOKIE_SECURE='1'" in body
    assert "AICRM_PUBLIC_BASE_URL='https://www.youcangogogo.com'" in body
    assert "AICRM_ALLOW_MISSING_WECHAT_SHOP_CALLBACK_TOKEN='1'" in body
    assert "AICRM_QUESTIONNAIRE_EXTERNAL_PUSH_MODE" not in body
    assert "AICRM_NEXT_WECOM_REAL_CALLS_ENABLED" not in body
    assert "AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE" not in body
    assert "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES" not in body
    assert "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS" not in body
