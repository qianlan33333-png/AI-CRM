from __future__ import annotations

import hashlib
from time import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from aicrm_next.channels.auth_wecom.service import sign_auth_state
from aicrm_next.channels.integration_gateway.wecom_jssdk_adapter import (
    build_sidebar_jssdk_config,
    list_sidebar_jssdk_attempts,
    normalize_jssdk_url,
    reset_sidebar_jssdk_attempts,
)
from aicrm_next.crm.identity_contact.sidebar_jssdk import SIDEBAR_VIEWER_COOKIE
from aicrm_next.main import create_app
from aicrm_next.shared.signed_context import load_sidebar_owner_context_token
from tests.sidebar_auth_test_helpers import install_sidebar_viewer_session


def test_fake_adapter_response_matches_frontend_contract(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setenv("WECOM_AGENT_ID", "1000002")
    reset_sidebar_jssdk_attempts()

    payload = build_sidebar_jssdk_config(url="http://127.0.0.1:5001/sidebar/bind-mobile", debug=True)

    assert payload["ok"] is True
    assert payload["corp_id"] == "ww-test"
    assert payload["appId"] == "ww-test"
    assert payload["agent_id"] == "1000002"
    assert payload["config"]["url"] == "http://127.0.0.1:5001/sidebar/bind-mobile"
    assert payload["config"]["nonceStr"]
    assert payload["config"]["signature"]
    assert payload["agent_config"]["signature"]
    assert payload["jsApiList"] == ["getContext", "getCurExternalContact", "sendChatMessage"]
    assert payload["source_status"] == "next_jssdk_adapter"
    assert payload["adapter_mode"] == "fake"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert list_sidebar_jssdk_attempts()[0]["event_type"] == "sidebar.jssdk.planned"


def test_production_default_is_real_blocked_without_real_call(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.delenv("AICRM_SIDEBAR_JSSDK_ADAPTER_MODE", raising=False)
    monkeypatch.delenv("AICRM_SIDEBAR_JSSDK_REAL_ENABLED", raising=False)
    reset_sidebar_jssdk_attempts()

    payload = build_sidebar_jssdk_config(url="/sidebar/bind-mobile")

    assert payload["adapter_mode"] == "real_blocked"
    assert payload["config"]["url"] == "http://localhost/sidebar/bind-mobile"
    assert payload["external_call_blocked"] is True
    assert payload["real_external_call_executed"] is False
    assert list_sidebar_jssdk_attempts()[0]["event_type"] == "sidebar.jssdk.blocked"


def test_real_enabled_adapter_fetches_wecom_signing_material(monkeypatch) -> None:
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setenv("WECOM_AGENT_ID", "1000002")
    monkeypatch.setenv("WECOM_SECRET", "secret")
    reset_sidebar_jssdk_attempts()
    calls: list[str] = []

    def fake_get_json(url: str, *, timeout: int) -> dict:
        calls.append(url)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if parsed.path == "/cgi-bin/gettoken":
            assert params["corpid"] == ["ww-test"]
            assert params["corpsecret"] == ["secret"]
            return {"errcode": 0, "access_token": "token-1", "expires_in": 7200}
        if parsed.path == "/cgi-bin/get_jsapi_ticket":
            assert params["access_token"] == ["token-1"]
            return {"errcode": 0, "ticket": "corp-ticket", "expires_in": 7200}
        if parsed.path == "/cgi-bin/ticket/get":
            assert params["access_token"] == ["token-1"]
            assert params["type"] == ["agent_config"]
            return {"errcode": 0, "ticket": "agent-ticket", "expires_in": 7200}
        raise AssertionError(f"unexpected url: {url}")

    payload = build_sidebar_jssdk_config(
        url="https://www.youcangogogo.com/sidebar/bind-mobile",
        adapter_mode="real_enabled",
        http_get_json=fake_get_json,
    )

    assert payload["adapter_mode"] == "real_enabled"
    assert payload["real_external_call_executed"] is True
    assert payload["external_call_blocked"] is False
    assert payload["fallback_used"] is False
    assert len(calls) == 3
    assert _expected_signature(payload["config"], "corp-ticket") == payload["config"]["signature"]
    assert _expected_signature(payload["agent_config"], "agent-ticket") == payload["agent_config"]["signature"]
    assert list_sidebar_jssdk_attempts()[0]["event_type"] == "sidebar.jssdk.success"


def test_jssdk_api_get_head_and_options_are_next_owned(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-jssdk-api")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    get_response = client.get("/api/sidebar/jssdk-config", params={"url": "http://127.0.0.1:5001/sidebar/bind-mobile"})
    options_response = client.options("/api/sidebar/jssdk-config")
    head_response = client.head("/api/sidebar/jssdk-config", params={"url": "http://127.0.0.1:5001/sidebar/bind-mobile"})

    assert get_response.status_code == 200
    assert get_response.json()["source_status"] == "next_jssdk_adapter"
    assert get_response.json()["fallback_used"] is False
    assert "X-AICRM-Compatibility-Facade" not in get_response.headers
    assert options_response.status_code == 200
    assert options_response.json()["source_status"] == "next_jssdk_adapter"
    assert "X-AICRM-Compatibility-Facade" not in options_response.headers
    assert head_response.status_code == 204
    assert "X-AICRM-Compatibility-Facade" not in head_response.headers


def test_jssdk_api_rejects_query_claimed_viewer_without_oauth_session(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(
        "/api/sidebar/jssdk-config",
        params={
            "url": "http://127.0.0.1:5001/sidebar/bind-mobile",
            "external_userid": "wx_ext_001",
            "viewer_userid": "ZhaoYanFang",
            "bind_by_userid": "ZhaoYanFang",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sidebar_owner_token"] == ""
    assert payload["sidebar_owner_token_status"] == "viewer_session_required"


def test_jssdk_api_rejects_single_owner_identity_fallback_without_oauth_session(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-token-external")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(
        "/api/sidebar/jssdk-config",
        params={
            "url": "http://127.0.0.1:5001/sidebar/bind-mobile",
            "external_userid": "wx_ext_001",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sidebar_owner_token_status"] == "viewer_session_required"
    assert payload["sidebar_owner_context"]["external_userid"] == "wx_ext_001"
    assert payload["sidebar_owner_context"]["source"] == "sidebar_jssdk_viewer_required"
    assert payload["sidebar_owner_token"] == ""


def test_jssdk_api_requires_oauth_viewer_session_for_external_contact(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-token-multi-owner")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(
        "/api/sidebar/jssdk-config",
        params={
            "url": "http://127.0.0.1:5001/sidebar/bind-mobile",
            "external_userid": "wx_ext_multi_owner",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sidebar_owner_token"] == ""
    assert payload["sidebar_owner_token_status"] == "viewer_session_required"
    assert payload["sidebar_owner_context"] == {
        "external_userid": "wx_ext_multi_owner",
        "source": "sidebar_jssdk_viewer_required",
        "sidebar_oauth_status": "disabled",
    }


def test_jssdk_api_exposes_sidebar_oauth_when_multi_owner_viewer_is_missing(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-token-multi-owner-oauth")
    monkeypatch.setenv("AICRM_SIDEBAR_WECOM_OAUTH_ENABLE_REAL", "1")
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setenv("WECOM_SECRET", "secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False, base_url="https://www.youcangogogo.com")

    response = client.get(
        "/api/sidebar/jssdk-config",
        params={
            "url": "https://www.youcangogogo.com/sidebar/bind-mobile?external_userid=wx_ext_multi_owner",
            "external_userid": "wx_ext_multi_owner",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sidebar_owner_token_status"] == "viewer_session_required"
    assert payload["sidebar_owner_context"]["sidebar_oauth_status"] == "ready"
    assert payload["sidebar_oauth_url"].startswith("/api/sidebar/oauth/start?")
    oauth_query = parse_qs(urlparse(payload["sidebar_oauth_url"]).query)
    assert oauth_query["external_userid"] == ["wx_ext_multi_owner"]
    assert oauth_query["next"] == ["/sidebar/bind-mobile?external_userid=wx_ext_multi_owner"]


def test_jssdk_api_issues_viewer_token_for_oauth_bound_session(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-token-viewer-candidate")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_sidebar_viewer_session(
        client,
        viewer_userid="HuangYouCan",
        external_userid="wx_ext_multi_owner",
    )

    response = client.get(
        "/api/sidebar/jssdk-config",
        params={
            "url": "http://127.0.0.1:5001/sidebar/bind-mobile",
            "external_userid": "wx_ext_multi_owner",
            "viewer_userid": "HuangYouCan",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sidebar_owner_token_status"] == "issued"
    assert payload["sidebar_owner_context"]["owner_userid"] == "HuangYouCan"
    token_result = load_sidebar_owner_context_token(payload["sidebar_owner_token"])
    assert token_result["ok"] is True
    assert token_result["context"]["viewer_userid"] == "HuangYouCan"


def test_jssdk_api_does_not_trust_admin_session_as_sidebar_oauth(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-token-admin-session")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(
        "/api/sidebar/jssdk-config",
        params={
            "url": "http://127.0.0.1:5001/sidebar/bind-mobile",
            "external_userid": "wx_ext_multi_owner",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sidebar_owner_token_status"] == "viewer_session_required"
    assert payload["sidebar_owner_token"] == ""


def test_sidebar_oauth_callback_sets_viewer_cookie_and_unblocks_owner_token(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-oauth-cookie")
    monkeypatch.setenv("AICRM_SIDEBAR_WECOM_OAUTH_ENABLE_REAL", "1")
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setenv("WECOM_SECRET", "secret")
    monkeypatch.setenv("AICRM_ADMIN_SESSION_COOKIE_SECURE", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    class FakeWeComAuthClient:
        def fetch_access_token(self, *, corp_id: str, corp_secret: str) -> dict:
            assert corp_id == "ww-test"
            assert corp_secret == "secret"
            return {"errcode": 0, "access_token": "access-token"}

        def fetch_user_info(self, *, access_token: str, code: str) -> dict:
            assert access_token == "access-token"
            assert code == "oauth-code"
            return {"errcode": 0, "UserId": "HuangYouCan"}

    monkeypatch.setattr(
        "aicrm_next.crm.identity_contact.sidebar_jssdk.build_wecom_admin_auth_client",
        lambda: FakeWeComAuthClient(),
    )
    client = TestClient(create_app(), raise_server_exceptions=False, base_url="https://www.youcangogogo.com")
    state = sign_auth_state(
        {
            "external_userid": "wx_ext_multi_owner",
            "next": "/sidebar/bind-mobile?external_userid=wx_ext_multi_owner",
            "nonce": "nonce",
            "iat": int(time()),
        }
    )

    callback = client.get(
        "/api/sidebar/oauth/callback",
        params={"state": state, "code": "oauth-code"},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/sidebar/bind-mobile?external_userid=wx_ext_multi_owner&sidebar_oauth=1"
    assert SIDEBAR_VIEWER_COOKIE in callback.headers["set-cookie"]
    response = client.get(
        "/api/sidebar/jssdk-config",
        params={
            "url": "https://www.youcangogogo.com/sidebar/bind-mobile?external_userid=wx_ext_multi_owner&sidebar_oauth=1",
            "external_userid": "wx_ext_multi_owner",
        },
    )
    payload = response.json()
    assert payload["sidebar_owner_token_status"] == "issued"
    assert payload["sidebar_owner_context"]["owner_userid"] == "HuangYouCan"


def test_sidebar_oauth_callback_trusts_oauth_viewer_without_relationship_lookup(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-oauth-scope")
    monkeypatch.setenv("AICRM_SIDEBAR_WECOM_OAUTH_ENABLE_REAL", "1")
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setenv("WECOM_SECRET", "secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "aicrm_next.crm.identity_contact.application.ListExternalContactOwnerCandidatesQuery.execute",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("relationship lookup must not run")),
    )

    class FakeWeComAuthClient:
        def fetch_access_token(self, *, corp_id: str, corp_secret: str) -> dict:
            return {"errcode": 0, "access_token": "access-token"}

        def fetch_user_info(self, *, access_token: str, code: str) -> dict:
            return {"errcode": 0, "UserId": "OtherOwner"}

    monkeypatch.setattr(
        "aicrm_next.crm.identity_contact.sidebar_jssdk.build_wecom_admin_auth_client",
        lambda: FakeWeComAuthClient(),
    )
    client = TestClient(create_app(), raise_server_exceptions=False)
    state = sign_auth_state(
        {
            "external_userid": "wx_ext_multi_owner",
            "next": "/sidebar/bind-mobile?external_userid=wx_ext_multi_owner",
            "nonce": "nonce",
            "iat": int(time()),
        }
    )

    callback = client.get(
        "/api/sidebar/oauth/callback",
        params={"state": state, "code": "oauth-code"},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/sidebar/bind-mobile?external_userid=wx_ext_multi_owner&sidebar_oauth=1"
    assert SIDEBAR_VIEWER_COOKIE in callback.headers.get("set-cookie", "")


def test_jssdk_api_issues_token_without_relationship_lookup(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-owner-token-viewer-rejected")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "aicrm_next.crm.identity_contact.application.ListExternalContactOwnerCandidatesQuery.execute",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("relationship lookup must not run")),
    )
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_sidebar_viewer_session(
        client,
        viewer_userid="OtherOwner",
        external_userid="wx_ext_multi_owner",
    )

    response = client.get(
        "/api/sidebar/jssdk-config",
        params={
            "url": "http://127.0.0.1:5001/sidebar/bind-mobile",
            "external_userid": "wx_ext_multi_owner",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sidebar_owner_token"]
    assert payload["sidebar_owner_token_status"] == "issued"
    assert payload["sidebar_owner_context"]["owner_userid"] == "OtherOwner"


def test_jssdk_api_rejects_unallowed_signing_host_in_production(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-jssdk-host-guard")
    monkeypatch.setenv("WECHAT_SHOP_CALLBACK_TOKEN", "sidebar-jssdk-host-guard-token")
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_SIDEBAR_JSSDK_ALLOWED_HOSTS", "crm.example.com")
    client = TestClient(create_app(), raise_server_exceptions=False, base_url="https://crm.example.com")

    response = client.get("/api/sidebar/jssdk-config", params={"url": "https://evil.example.com/sidebar/bind-mobile"})

    assert response.status_code == 400
    payload = response.json()
    assert payload["source_status"] == "input_error"
    assert payload["real_external_call_executed"] is False
    assert "url host is not allowed" in payload["error"]


def test_jssdk_url_validation_accepts_relative_and_rejects_non_http() -> None:
    assert normalize_jssdk_url("/sidebar/bind-mobile") == "http://localhost/sidebar/bind-mobile"

    try:
        normalize_jssdk_url("javascript:alert(1)")
    except ValueError as exc:
        assert "http(s)" in str(exc)
    else:
        raise AssertionError("expected invalid URL to fail")


def _expected_signature(config: dict, ticket: str) -> str:
    plain = "&".join(
        [
            f"jsapi_ticket={ticket}",
            f"noncestr={config['nonceStr']}",
            f"timestamp={config['timestamp']}",
            f"url={config['url']}",
        ]
    )
    return hashlib.sha1(plain.encode("utf-8")).hexdigest()
