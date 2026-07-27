from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.extensions.forms.questionnaire.oauth import reset_questionnaire_oauth_state


WECHAT_UA = "Mozilla/5.0 MicroMessenger/8.0.49"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ADAPTER_MODE", raising=False)
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ENABLE_REAL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "questionnaire-oauth-browser-test")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-browser-test")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "wx-browser-secret")
    reset_questionnaire_oauth_state()
    h5_wechat_pay.reset_h5_wechat_pay_oauth_client_factory()
    client = TestClient(create_app())
    yield client
    h5_wechat_pay.reset_h5_wechat_pay_oauth_client_factory()


def test_oauth_start_default_still_returns_json(client: TestClient) -> None:
    response = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["ok"] is True
    assert body["adapter_mode"] == "fake"


def test_oauth_start_redirect_mode_returns_location(client: TestClient) -> None:
    response = client.get(
        "/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1&response_mode=redirect",
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("/api/h5/wechat/oauth/callback?")
    query = parse_qs(urlparse(location).query)
    assert query["code"] == ["fake-code"]
    assert query["response_mode"] == ["redirect"]
    assert query["state"][0].count(".") == 1


def test_h5_auth_gate_uses_browser_redirect_oauth_start_url(client: TestClient) -> None:
    response = client.get(
        "/s/hxc-activation-v1?source_channel=wechat&campaign_id=cmp_001&staff_id=staff_001",
        headers={"User-Agent": WECHAT_UA},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "/api/h5/wechat-pay/oauth/start?"
        "return_url=%2Fs%2Fhxc-activation-v1%3Fsource_channel%3Dwechat%26campaign_id%3Dcmp_001%26staff_id%3Dstaff_001"
    )


def test_h5_submit_without_wechat_identity_returns_oauth_redirect(client: TestClient) -> None:
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={"answers": {"q_activation": "activated"}},
        headers={"User-Agent": WECHAT_UA},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "unionid_oauth_required"
    assert body["message"] == "请先完成微信授权，获取稳定身份后继续。"
    assert body["redirect_url"] == "/api/h5/wechat-pay/oauth/start?return_url=%2Fs%2Fhxc-activation-v1"
    assert body["write_model_status"] == "blocked"
    assert body["real_external_call_executed"] is False


def test_h5_page_handles_oauth_required_submit_without_exposing_raw_error(client: TestClient) -> None:
    response = client.get("/s/hxc-activation-v1", params={"openid": "openid-next-public-001"})

    assert response.status_code == 200
    html = response.text
    assert "function startOAuthRedirect" in html
    assert "oauth_required_redirect" in html
    assert "请先完成微信授权" in html
    assert html.index("if (isOAuthRequired(result))") < html.index("throw new Error(result.error || '提交失败')")


def test_oauth_callback_default_still_returns_json_and_cookie(client: TestClient) -> None:
    state = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1").json()["state"]

    response = client.get(f"/api/h5/wechat/oauth/callback?code=fake-code&state={state}", follow_redirects=False)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["ok"] is True
    assert "questionnaire_h5_identity=" in response.headers["set-cookie"]


def test_oauth_callback_browser_redirect_sets_cookie_on_redirect_response(client: TestClient) -> None:
    start = client.get(
        "/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1&response_mode=redirect",
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    response = client.get(
        f"/api/h5/wechat/oauth/callback?code=fake-code&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/s/hxc-activation-v1"
    assert "questionnaire_h5_identity=" in response.headers["set-cookie"]
    assert response.text == ""


def test_oauth_callback_browser_redirect_error_returns_readable_html(client: TestClient) -> None:
    response = client.get(
        "/api/h5/wechat/oauth/callback?code=fake-code&state=bad-state&response_mode=redirect",
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "授权未完成，请重新进入问卷" in response.text
    assert '{"ok":' not in response.text


def test_real_blocked_browser_redirect_returns_readable_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ADAPTER_MODE", raising=False)
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ENABLE_REAL", raising=False)
    reset_questionnaire_oauth_state()
    client = TestClient(create_app())

    response = client.get(
        "/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1&response_mode=redirect",
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert "当前微信授权配置未完成，请联系管理员" in response.text
    assert '{"ok":' not in response.text
    assert "state=" not in response.text
    assert 'href="/s/hxc-activation-v1"' in response.text


def test_real_enabled_browser_start_redirects_to_wechat_authorize_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.setenv("AICRM_QUESTIONNAIRE_OAUTH_ADAPTER_MODE", "real_enabled")
    monkeypatch.setenv("AICRM_QUESTIONNAIRE_OAUTH_ENABLE_REAL", "1")
    monkeypatch.setenv("AICRM_PUBLIC_BASE_URL", "https://crm.example.test")
    monkeypatch.setenv("SECRET_KEY", "questionnaire-oauth-real-enabled-test")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-real-enabled")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "wx-real-secret")
    reset_questionnaire_oauth_state()
    client = TestClient(create_app())

    response = client.get(
        "/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1&response_mode=redirect",
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://open.weixin.qq.com/connect/oauth2/authorize?")
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert query["appid"] == ["wx-real-enabled"]
    assert query["redirect_uri"] == ["https://crm.example.test/api/h5/wechat/oauth/callback"]
    assert query["state"][0].count(".") == 1
    assert location.endswith("#wechat_redirect")


def test_wechat_h5_gate_enters_questionnaire_after_browser_oauth_cookie(client: TestClient) -> None:
    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            return {"openid": "openid_questionnaire", "unionid": "unionid_questionnaire"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            return {"openid": openid, "unionid": "unionid_questionnaire"}

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    gate = client.get(
        "/s/hxc-activation-v1",
        headers={"User-Agent": WECHAT_UA},
        follow_redirects=False,
    )
    assert gate.status_code == 302
    assert gate.headers["location"] == "/api/h5/wechat-pay/oauth/start?return_url=%2Fs%2Fhxc-activation-v1"

    start = client.get(
        "/api/h5/wechat-pay/oauth/start?return_url=/s/hxc-activation-v1",
        follow_redirects=False,
    )
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": "questionnaire-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/s/hxc-activation-v1"
    assert "wechat_pay_h5_identity=" in callback.headers["set-cookie"]

    final = client.get("/s/hxc-activation-v1", headers={"User-Agent": WECHAT_UA})
    assert final.status_code == 200
    assert '"mode": "questionnaire"' in final.text
    assert '"is_authorized": true' in final.text
    assert "questionnaire-form" in final.text
