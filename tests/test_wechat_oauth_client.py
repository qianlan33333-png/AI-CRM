from __future__ import annotations

from pathlib import Path

import pytest

from aicrm_next.channels.integration_gateway.wechat_oauth_client import WeChatOAuthClient, WeChatOAuthClientError


class FakeResponse:
    def __init__(self, payload=None, *, json_error: Exception | None = None) -> None:
        self._payload = payload
        self._json_error = json_error

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


def test_wechat_oauth_client_exchange_code_path_and_params() -> None:
    calls: list[dict] = []

    def fake_http_get(url: str, *, params: dict, timeout: int | float):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse({"openid": "openid_001", "unionid": "union_001", "access_token": "token_001"})

    client = WeChatOAuthClient(timeout=7, http_get=fake_http_get, oauth_base_url="https://wechat.example.com")

    payload = client.exchange_code(app_id="wx_001", app_secret="secret_001", code="code_001")

    assert payload == {"openid": "openid_001", "unionid": "union_001", "access_token": "token_001"}
    assert calls == [
        {
            "url": "https://wechat.example.com/sns/oauth2/access_token",
            "params": {
                "appid": "wx_001",
                "secret": "secret_001",
                "code": "code_001",
                "grant_type": "authorization_code",
            },
            "timeout": 7,
        }
    ]


def test_wechat_oauth_client_fetch_userinfo_path_and_params() -> None:
    calls: list[dict] = []

    def fake_http_get(url: str, *, params: dict, timeout: int | float):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse({"openid": "openid_001", "unionid": "union_001"})

    client = WeChatOAuthClient(timeout=9, http_get=fake_http_get, oauth_base_url="https://wechat.example.com/")

    payload = client.fetch_userinfo(access_token="token_001", openid="openid_001")

    assert payload == {"openid": "openid_001", "unionid": "union_001"}
    assert calls == [
        {
            "url": "https://wechat.example.com/sns/userinfo",
            "params": {"access_token": "token_001", "openid": "openid_001", "lang": "zh_CN"},
            "timeout": 9,
        }
    ]


def test_wechat_oauth_client_http_exception_fails() -> None:
    def fake_http_get(url: str, *, params: dict, timeout: int | float):
        raise RuntimeError("network unavailable")

    client = WeChatOAuthClient(http_get=fake_http_get)

    with pytest.raises(WeChatOAuthClientError) as exc:
        client.exchange_code(app_id="wx_001", app_secret="secret_001", code="code_001")

    assert exc.value.error_code == "wechat_oauth_http_error"
    assert exc.value.payload["endpoint"] == "/sns/oauth2/access_token"


@pytest.mark.parametrize("response", [FakeResponse(json_error=ValueError("bad json")), FakeResponse(["not", "a", "dict"])])
def test_wechat_oauth_client_invalid_json_fails(response: FakeResponse) -> None:
    def fake_http_get(url: str, *, params: dict, timeout: int | float):
        return response

    client = WeChatOAuthClient(http_get=fake_http_get)

    with pytest.raises(WeChatOAuthClientError) as exc:
        client.fetch_userinfo(access_token="token_001", openid="openid_001")

    assert exc.value.error_code == "wechat_oauth_response_invalid"


def test_wechat_oauth_client_does_not_import_legacy() -> None:
    source = Path("aicrm_next/channels/integration_gateway/wechat_oauth_client.py").read_text(encoding="utf-8")

    assert "wecom_ability" + "_service" not in source
    assert "legacy_flask_facade" not in source
    assert "current_app" not in source
