from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from aicrm_next.channels.integration_gateway.wechat_oauth_client import WeChatOAuthClientError
from aicrm_next.main import create_app
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", "h5-pay-oauth-native-test-secret")
    monkeypatch.setenv("SECRET_KEY", "h5-pay-oauth-native-fallback-secret")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-pay-oauth-app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "pay-oauth-app-secret")
    return TestClient(create_app(), raise_server_exceptions=False, base_url="https://pay.example.test")


def _start_state(client: TestClient, return_url: str = "/pay/demo") -> str:
    response = client.get(
        "/api/h5/wechat-pay/oauth/start",
        params={"return_url": return_url},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def _state_cookie(
    *,
    nonce: str,
    return_url: str = "/pay/demo",
    issued_at: int | None = None,
    expires_at: int | None = None,
) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    issued = now if issued_at is None else issued_at
    return h5_wechat_pay._signed_blob(
        {
            "return_url": return_url,
            "nonce": nonce,
            "iat": issued,
            "exp": issued + h5_wechat_pay.STATE_TTL_SECONDS if expires_at is None else expires_at,
        }
    )


def _cookie_payload(client: TestClient) -> dict:
    cookie = client.cookies.get(h5_wechat_pay.COOKIE_NAME)
    assert cookie
    return h5_wechat_pay._load_signed_blob(cookie)


@pytest.fixture(autouse=True)
def _reset_oauth_client_factory():
    h5_wechat_pay.reset_h5_wechat_pay_oauth_client_factory()
    yield
    h5_wechat_pay.reset_h5_wechat_pay_oauth_client_factory()


def test_h5_pay_oauth_start_preserves_userinfo_scope(monkeypatch) -> None:
    monkeypatch.delenv("WECHAT_PAY_OAUTH_SCOPE", raising=False)
    response = _client(monkeypatch).get(
        "/api/h5/wechat-pay/oauth/start?return_url=/pay/demo",
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert query["scope"] == ["snsapi_userinfo"]
    assert query["appid"] == ["wx-pay-oauth-app"]
    assert query["redirect_uri"] == ["https://pay.example.test/api/h5/wechat-pay/oauth/callback"]
    assert query["state"][0]
    assert len(query["state"][0].encode("ascii")) <= 128
    assert query["state"][0].isalnum()
    state_cookie = response.headers["set-cookie"]
    assert f"{h5_wechat_pay.OAUTH_STATE_COOKIE_NAME}=" in state_cookie
    assert "HttpOnly" in state_cookie
    assert "Secure" in state_cookie
    assert "SameSite=lax" in state_cookie
    assert f"Path={h5_wechat_pay.OAUTH_STATE_COOKIE_PATH}" in state_cookie
    assert "Domain=" not in state_cookie


def test_h5_pay_oauth_callback_rejects_state_without_browser_cookie(monkeypatch) -> None:
    client = _client(monkeypatch)
    nonce = "a" * 48

    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": nonce, "code": "must-not-be-exchanged"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "oauth_state_cookie_missing"
    assert h5_wechat_pay.COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_h5_pay_oauth_callback_rejects_state_cookie_mismatch_before_exchange(monkeypatch) -> None:
    class OAuthClientMustNotRun:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            raise AssertionError("mismatched browser state must be rejected before token exchange")

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: OAuthClientMustNotRun())
    client = _client(monkeypatch)
    cookie_nonce = "b" * 48
    query_nonce = "c" * 48
    client.cookies.set(
        h5_wechat_pay.OAUTH_STATE_COOKIE_NAME,
        _state_cookie(nonce=cookie_nonce),
        path=h5_wechat_pay.OAUTH_STATE_COOKIE_PATH,
    )

    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": query_nonce, "code": "must-not-be-exchanged"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "oauth_state_cookie_mismatch"


def test_h5_pay_oauth_callback_rejects_tampered_state_cookie(monkeypatch) -> None:
    nonce = "d" * 48
    signed_cookie = _state_cookie(nonce=nonce)
    client = _client(monkeypatch)
    client.cookies.set(
        h5_wechat_pay.OAUTH_STATE_COOKIE_NAME,
        signed_cookie[:-1] + ("0" if signed_cookie[-1] != "0" else "1"),
        path=h5_wechat_pay.OAUTH_STATE_COOKIE_PATH,
    )

    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": nonce, "code": "must-not-be-exchanged"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "oauth_state_cookie_invalid"


def test_h5_pay_oauth_callback_rejects_expired_state_cookie_and_clears_it(monkeypatch) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    nonce = "e" * 48
    client = _client(monkeypatch)
    client.cookies.set(
        h5_wechat_pay.OAUTH_STATE_COOKIE_NAME,
        _state_cookie(
            nonce=nonce,
            issued_at=now - h5_wechat_pay.STATE_TTL_SECONDS,
            expires_at=now - 1,
        ),
        path=h5_wechat_pay.OAUTH_STATE_COOKIE_PATH,
    )

    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": nonce, "code": "must-not-be-exchanged"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "state_expired"
    assert h5_wechat_pay.OAUTH_STATE_COOKIE_NAME in response.headers.get("set-cookie", "")
    assert "Max-Age=0" in response.headers.get("set-cookie", "")


def test_h5_pay_oauth_callback_uses_native_client_and_sets_cookie(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            calls["exchange"] = {"app_id": app_id, "app_secret": app_secret, "code": code}
            return {"openid": "op_pay_001", "unionid": "un_pay_001", "access_token": "token_should_not_be_cookie"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            calls["userinfo"] = {"access_token": access_token, "openid": openid}
            return {"openid": "op_pay_001", "unionid": "un_pay_001", "nickname": "支付昵称"}

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)
    state = _start_state(client, "/pay/demo")
    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": "oauth-code-001"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/pay/demo"
    assert h5_wechat_pay.COOKIE_NAME in response.headers["set-cookie"]
    assert h5_wechat_pay.OAUTH_STATE_COOKIE_NAME in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    payload = _cookie_payload(client)
    assert payload["openid"] == "op_pay_001"
    assert payload["unionid"] == "un_pay_001"
    assert payload["payer_name"] == "支付昵称"
    assert payload["exp"] - payload["iat"] == h5_wechat_pay.WECHAT_PAYMENT_IDENTITY_TTL_SECONDS
    assert "access_token" not in payload
    assert calls["exchange"] == {
        "app_id": "wx-pay-oauth-app",
        "app_secret": "pay-oauth-app-secret",
        "code": "oauth-code-001",
    }
    assert calls["userinfo"] == {"access_token": "token_should_not_be_cookie", "openid": "op_pay_001"}


def test_h5_pay_oauth_state_cookie_is_consumed_once(monkeypatch) -> None:
    exchange_calls: list[str] = []

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            exchange_calls.append(code)
            return {"openid": "op_once", "unionid": "un_once", "access_token": "token_once"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            return {"openid": openid, "unionid": "un_once", "nickname": "一次性 OAuth"}

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)
    state = _start_state(client)
    params = {"state": state, "code": "oauth-code-once"}

    first = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params=params,
        follow_redirects=False,
    )
    second = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params=params,
        follow_redirects=False,
    )

    assert first.status_code == 302
    assert second.status_code == 400
    assert second.json()["error"] == "oauth_state_cookie_missing"
    assert exchange_calls == ["oauth-code-once"]


def test_h5_pay_oauth_callback_resolves_secret_reference_before_exchange(monkeypatch) -> None:
    calls: dict[str, str] = {}

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            calls.update(app_id=app_id, app_secret=app_secret, code=code)
            return {"openid": "op_secretref", "unionid": "un_secretref", "access_token": "token_secretref"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            return {"openid": openid, "unionid": "un_secretref", "nickname": "引用密钥用户"}

    client = _client(monkeypatch)
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "secretref:file:WECHAT_MP_APP_SECRET:v1_reference")

    def resolve_setting(name: str, default: str = "") -> str:
        if name == "WECHAT_MP_APP_SECRET":
            return "resolved-wechat-mp-secret"
        return os.getenv(name, default)

    monkeypatch.setattr(h5_wechat_pay, "runtime_setting", resolve_setting)
    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())

    state = _start_state(client)
    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": "oauth-code-secretref"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert calls == {
        "app_id": "wx-pay-oauth-app",
        "app_secret": "resolved-wechat-mp-secret",
        "code": "oauth-code-secretref",
    }


def test_h5_pay_client_config_resolves_sensitive_secret_references(monkeypatch) -> None:
    monkeypatch.setenv("WECHAT_PAY_API_V3_KEY", "secretref:file:WECHAT_PAY_API_V3_KEY:v1_reference")
    monkeypatch.setenv("WECHAT_PAY_CERT_SERIAL_NO", "secretref:file:WECHAT_PAY_CERT_SERIAL_NO:v1_reference")

    def resolve_setting(name: str, default: str = "") -> str:
        resolved = {
            "WECHAT_PAY_API_V3_KEY": "resolved-api-v3-key",
            "WECHAT_PAY_CERT_SERIAL_NO": "resolved-merchant-serial",
        }
        return resolved.get(name, os.getenv(name, default))

    monkeypatch.setattr(h5_wechat_pay, "runtime_setting", resolve_setting)

    config = h5_wechat_pay._client_config()

    assert config.api_v3_key == "resolved-api-v3-key"
    assert config.merchant_serial_no == "resolved-merchant-serial"


def test_h5_pay_oauth_start_skips_wechat_when_identity_cookie_exists(monkeypatch) -> None:
    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            return {"openid": "op_pay_cached", "unionid": "un_pay_cached", "access_token": "token_cached"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            return {"openid": openid, "unionid": "un_pay_cached", "nickname": "已授权用户"}

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)
    state = _start_state(client, "/pay/demo")
    callback = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": "oauth-code-cached"},
        follow_redirects=False,
    )
    assert callback.status_code == 302

    response = client.get(
        "/api/h5/wechat-pay/oauth/start?return_url=/pay/demo",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/pay/demo"
    assert "open.weixin.qq.com" not in response.headers["location"]


def test_h5_pay_oauth_start_ignores_untrusted_forwarded_host(monkeypatch) -> None:
    for setting in ("AICRM_PUBLIC_BASE_URL", "PUBLIC_BASE_URL", "APP_BASE_URL"):
        monkeypatch.delenv(setting, raising=False)
    response = _client(monkeypatch).get(
        "/api/h5/wechat-pay/oauth/start?return_url=/pay/demo",
        headers={
            "X-Forwarded-Host": "attacker.example",
            "X-Forwarded-Proto": "http",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    redirect_uri = parse_qs(urlparse(response.headers["location"]).query)["redirect_uri"][0]
    assert redirect_uri == "https://pay.example.test/api/h5/wechat-pay/oauth/callback"
    assert "attacker.example" not in response.headers["location"]


def test_h5_pay_expired_identity_cookie_requires_fresh_oauth(monkeypatch) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    client = _client(monkeypatch)
    client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob(
            {
                "openid": "op_expired_cookie",
                "unionid": "un_expired_cookie",
                "iat": now - 600,
                "exp": now - 1,
            }
        ),
    )

    response = client.get(
        "/api/h5/wechat-pay/oauth/start?return_url=/pay/demo",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "open.weixin.qq.com" in response.headers["location"]


def test_h5_pay_production_without_session_secret_fails_closed(monkeypatch) -> None:
    client = _client(monkeypatch)
    signed_before_secret_removal = h5_wechat_pay._signed_blob(
        {"openid": "op_forged_candidate", "unionid": "un_forged_candidate"}
    )
    client.cookies.set(h5_wechat_pay.COOKIE_NAME, signed_before_secret_removal)
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.delenv("AICRM_NEXT_ACTION_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    response = client.get(
        "/api/h5/wechat-pay/oauth/start?return_url=/pay/demo",
        follow_redirects=False,
    )

    assert response.status_code == 501
    assert response.json()["error"] == "wechat_pay_oauth_not_configured"


def test_h5_pay_production_requires_canonical_public_base_url(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    for setting in ("AICRM_PUBLIC_BASE_URL", "PUBLIC_BASE_URL", "APP_BASE_URL"):
        monkeypatch.delenv(setting, raising=False)

    response = client.get(
        "/api/h5/wechat-pay/oauth/start?return_url=/pay/demo",
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.json()["error"] == "public_base_url_not_configured"


def test_h5_pay_production_rejects_insecure_public_base_url(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AICRM_PUBLIC_BASE_URL", "http://pay.example.test")

    response = client.get(
        "/api/h5/wechat-pay/oauth/start?return_url=/pay/demo",
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.json()["error"] == "public_base_url_not_configured"
    assert h5_wechat_pay.COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_questionnaire_query_identity_cookie_is_never_a_payment_credential(monkeypatch) -> None:
    client = _client(monkeypatch)
    anonymous = client.get("/api/h5/questionnaires/hxc-activation-v1")
    assert anonymous.status_code == 403
    assert anonymous.json()["error"] == "wechat_browser_required"
    assert "questionnaire_h5_identity=" not in anonymous.headers.get("set-cookie", "")
    questionnaire = client.get(
        "/api/h5/questionnaires/hxc-activation-v1",
        params={
            "openid": "attacker-controlled-openid",
            "unionid": "victim-unionid",
        },
        headers={"User-Agent": "Mozilla/5.0 MicroMessenger/8.0"},
        follow_redirects=False,
    )
    assert questionnaire.status_code == 401
    assert questionnaire.json()["error"] == "unionid_oauth_required"
    assert "questionnaire_h5_identity=" not in questionnaire.headers.get("set-cookie", "")
    assert not client.cookies.get(h5_wechat_pay.COOKIE_NAME)

    payment_start = client.get(
        "/api/h5/wechat-pay/oauth/start?return_url=/pay/demo",
        follow_redirects=False,
    )

    assert payment_start.status_code == 302
    assert "open.weixin.qq.com" in payment_start.headers["location"]


def test_h5_pay_oauth_callback_fetches_userinfo_when_unionid_missing(monkeypatch) -> None:
    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            return {"openid": "op_pay_002", "access_token": "token_002"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            return {"openid": openid, "unionid": "un_from_userinfo", "nickname": "用户信息昵称"}

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)
    state = _start_state(client)
    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": "oauth-code-002"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    payload = _cookie_payload(client)
    assert payload["openid"] == "op_pay_002"
    assert payload["unionid"] == "un_from_userinfo"
    assert payload["payer_name"] == "用户信息昵称"


def test_h5_pay_oauth_callback_base_scope_does_not_fetch_userinfo(monkeypatch) -> None:
    monkeypatch.setenv("WECHAT_PAY_OAUTH_SCOPE", "snsapi_base")

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            return {"openid": "op_pay_base", "unionid": "un_pay_base", "access_token": "token_base"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            raise AssertionError("userinfo must not be fetched for snsapi_base")

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)
    state = _start_state(client)
    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": "oauth-code-base"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    payload = _cookie_payload(client)
    assert payload["openid"] == "op_pay_base"
    assert payload["unionid"] == "un_pay_base"
    assert payload["payer_name"] == ""


def test_h5_pay_oauth_callback_missing_unionid_stops_on_identity_failure_page(monkeypatch) -> None:
    monkeypatch.setenv("WECHAT_PAY_OAUTH_SCOPE", "snsapi_base")

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            return {"openid": "openid_without_unionid", "access_token": "token_without_unionid"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            raise AssertionError("userinfo must not be fetched for snsapi_base")

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)
    state = _start_state(client, "/pay/test-product")

    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": "openid-only-code"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")
    assert "微信未返回 UnionID" in response.text
    assert "location" not in response.headers
    assert h5_wechat_pay.COOKIE_NAME not in response.headers.get("set-cookie", "")
    assert "Max-Age=0" in response.headers.get("set-cookie", "")


def test_h5_pay_oauth_callback_client_error_is_controlled(monkeypatch) -> None:
    raw_code = "raw-sensitive-code"
    raw_secret = "pay-oauth-app-secret"
    raw_token = "sensitive-access-token"

    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            raise WeChatOAuthClientError(f"failed with {code} {app_secret} {raw_token}")

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)
    state = _start_state(client)
    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": raw_code},
        follow_redirects=False,
    )

    body = response.text
    assert response.status_code == 502
    assert response.json()["error"] == "wechat_oauth_failed"
    assert raw_code not in body
    assert raw_secret not in body
    assert raw_token not in body
    assert h5_wechat_pay.OAUTH_STATE_COOKIE_NAME in response.headers.get("set-cookie", "")
    assert "Max-Age=0" in response.headers.get("set-cookie", "")


def test_h5_pay_oauth_callback_wechat_error_payload(monkeypatch) -> None:
    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            return {"errcode": 40029, "errmsg": "invalid code"}

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)
    state = _start_state(client)
    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": "bad-code"},
        follow_redirects=False,
    )

    assert response.status_code == 502
    assert response.json()["error"] == "invalid code"


def test_h5_pay_oauth_callback_missing_openid_does_not_loop_to_checkout(monkeypatch) -> None:
    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            return {"access_token": "token_without_openid"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            raise AssertionError("userinfo cannot be fetched without openid")

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)
    state = _start_state(client, "/pay/test-product")
    response = client.get(
        "/api/h5/wechat-pay/oauth/callback",
        params={"state": state, "code": "missing-openid"},
        follow_redirects=False,
    )

    assert response.status_code == 502
    assert response.json()["error"] == "wechat_oauth_openid_missing"
    assert "location" not in response.headers
    assert h5_wechat_pay.COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_empty_material_checkout_oauth_round_trip_enters_pay_without_auth_loop(monkeypatch) -> None:
    class FakeOAuthClient:
        def exchange_code(self, *, app_id: str, app_secret: str, code: str):
            return {"openid": "op_empty_material", "unionid": "un_empty_material", "access_token": "token_empty_material"}

        def fetch_userinfo(self, *, access_token: str, openid: str):
            return {"openid": openid, "unionid": "un_empty_material", "nickname": "无素材支付用户"}

    h5_wechat_pay.set_h5_wechat_pay_oauth_client_factory(lambda: FakeOAuthClient())
    client = _client(monkeypatch)

    product = client.get("/p/test-product", follow_redirects=False)
    assert product.status_code == 302
    assert product.headers["location"] == "/pay/test-product"

    before_auth = client.get(
        product.headers["location"],
        headers={"User-Agent": "MicroMessenger"},
        follow_redirects=False,
    )
    assert before_auth.status_code == 302
    assert before_auth.headers["location"] == "/api/h5/wechat-pay/oauth/start?return_url=%2Fpay%2Ftest-product"

    start = client.get("/api/h5/wechat-pay/oauth/start?return_url=/pay/test-product", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(
        f"/api/h5/wechat-pay/oauth/callback?state={state}&code=empty-material-code",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/pay/test-product"
    assert h5_wechat_pay.COOKIE_NAME in callback.headers["set-cookie"]

    after_auth = client.get(callback.headers["location"], follow_redirects=False)
    assert after_auth.status_code == 200
    assert ">微信授权后继续</a>" not in after_auth.text
    assert '<button id="payButton"' in after_auth.text


def test_h5_pay_runtime_has_no_legacy_oauth_helper() -> None:
    source = Path("aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py").read_text(encoding="utf-8")
    forbidden = [
        "wecom_ability" + "_service.infra." + "wechat_oauth",
        "exchange_wechat_" + "oauth_code",
        "fetch_wechat_" + "userinfo",
        "WeChatOAuth" + "RequestError",
    ]

    for marker in forbidden:
        assert marker not in source
