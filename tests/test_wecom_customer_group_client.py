from __future__ import annotations

import pytest

from aicrm_next.channels.integration_gateway.wecom_customer_group_client import (
    WeComCustomerGroupClient,
    WeComCustomerGroupClientError,
)
from aicrm_next.platform.shared.wecom_runtime import SingleFlightAccessTokenProvider


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, headers: dict | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return dict(self._payload)


def test_customer_group_client_gettoken_and_create_group_message_task() -> None:
    calls = {"get": 0, "post": []}

    def fake_get(url, *, params, timeout):
        calls["get"] += 1
        assert url == "https://qyapi.example/cgi-bin/gettoken"
        assert params == {"corpid": "corp_001", "corpsecret": "secret_001"}
        assert timeout == 7
        return FakeResponse({"errcode": 0, "access_token": "token_001", "expires_in": 7200})

    def fake_post(url, *, params, json, timeout):
        calls["post"].append({"url": url, "params": params, "json": json, "timeout": timeout})
        return FakeResponse({"errcode": 0, "errmsg": "ok", "msgid": "msg_001"})

    client = WeComCustomerGroupClient(
        corp_id="corp_001",
        secret="secret_001",
        api_base="https://qyapi.example",
        timeout=7,
        http_get=fake_get,
        http_post=fake_post,
    )

    payload = {"sender": "owner_001", "chat_id_list": ["chat_001"]}
    assert client.create_group_message_task(payload) == {"errcode": 0, "errmsg": "ok", "msgid": "msg_001"}
    assert client.create_group_message_task(payload)["msgid"] == "msg_001"

    assert calls["get"] == 1
    assert calls["post"][0]["url"] == "https://qyapi.example/cgi-bin/externalcontact/add_msg_template"
    assert calls["post"][0]["params"] == {"access_token": "token_001"}
    assert calls["post"][0]["json"] == payload
    assert calls["post"][1]["params"] == {"access_token": "token_001"}


def test_customer_group_client_list_and_get_group_chat_paths() -> None:
    posts: list[dict] = []

    def fake_get(url, *, params, timeout):
        return {"errcode": 0, "access_token": "token_002", "expires_in": 7200}

    def fake_post(url, *, params, json, timeout):
        posts.append({"url": url, "json": json, "params": params})
        return {"errcode": 0, "errmsg": "ok"}

    client = WeComCustomerGroupClient(
        corp_id="corp_001",
        secret="secret_001",
        api_base="https://qyapi.example/",
        http_get=fake_get,
        http_post=fake_post,
    )

    client.list_group_chats({"owner_filter": {"userid_list": ["owner_001"]}, "limit": 10})
    client.get_group_chat("chat_001", need_name=0)

    assert posts[0]["url"] == "https://qyapi.example/cgi-bin/externalcontact/groupchat/list"
    assert posts[1]["url"] == "https://qyapi.example/cgi-bin/externalcontact/groupchat/get"
    assert posts[1]["json"] == {"chat_id": "chat_001", "need_name": 0}
    assert posts[1]["params"] == {"access_token": "token_002"}


def test_customer_group_client_missing_config_fails_without_http_call(monkeypatch) -> None:
    for key in ("AICRM_WECOM_GROUP_CORP_ID", "WECOM_CORP_ID", "AICRM_WECOM_GROUP_SECRET", "WECOM_SECRET", "WECOM_CONTACT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    called = {"get": False, "post": False}

    def fake_get(*args, **kwargs):
        called["get"] = True
        raise AssertionError("http_get must not be called")

    def fake_post(*args, **kwargs):
        called["post"] = True
        raise AssertionError("http_post must not be called")

    client = WeComCustomerGroupClient(corp_id="", secret="", http_get=fake_get, http_post=fake_post)

    with pytest.raises(WeComCustomerGroupClientError) as exc:
        client.get_access_token()

    assert exc.value.error_code == "wecom_group_client_missing_config"
    assert called == {"get": False, "post": False}


def test_customer_group_client_token_nonzero_fails() -> None:
    def fake_get(url, *, params, timeout):
        return {"errcode": 40013, "errmsg": "invalid corpid"}

    client = WeComCustomerGroupClient(
        corp_id="corp_bad",
        secret="secret_bad",
        http_get=fake_get,
        http_post=lambda *args, **kwargs: {},
    )

    with pytest.raises(WeComCustomerGroupClientError) as exc:
        client.get_access_token()

    assert exc.value.error_code == "wecom_group_client_token_error"
    assert exc.value.payload == {"errcode": 40013, "errmsg": "invalid corpid"}


def test_customer_group_client_http_exception_fails() -> None:
    def fake_get(url, *, params, timeout):
        return {"errcode": 0, "access_token": "token_003", "expires_in": 7200}

    def fake_post(url, *, params, json, timeout):
        raise RuntimeError("network down")

    client = WeComCustomerGroupClient(
        corp_id="corp_001",
        secret="secret_001",
        http_get=fake_get,
        http_post=fake_post,
    )

    with pytest.raises(WeComCustomerGroupClientError) as exc:
        client.create_group_message_task({"sender": "owner_001", "chat_id_list": ["chat_001"]})

    assert exc.value.stage == "/cgi-bin/externalcontact/add_msg_template"
    assert exc.value.error_code == "wecom_group_client_http_error"


def test_customer_group_clients_share_singleflight_token_provider() -> None:
    calls = {"get": 0}
    provider = SingleFlightAccessTokenProvider()

    def fake_get(url, *, params, timeout):
        calls["get"] += 1
        return {"errcode": 0, "access_token": "shared-token", "expires_in": 7200}

    kwargs = {
        "corp_id": "corp_001",
        "secret": "secret_001",
        "http_get": fake_get,
        "http_post": lambda *args, **kwargs: {"errcode": 0},
        "token_provider": provider,
    }
    first = WeComCustomerGroupClient(**kwargs)
    second = WeComCustomerGroupClient(**kwargs)

    assert first.get_access_token() == "shared-token"
    assert second.get_access_token() == "shared-token"
    assert calls["get"] == 1
    assert provider.snapshot()["refresh_succeeded"] == 1


def test_customer_group_client_invalid_token_refreshes_and_retries_once() -> None:
    tokens = iter(("expired-token", "fresh-token"))
    calls = {"get": 0, "post_tokens": []}

    def fake_get(url, *, params, timeout):
        calls["get"] += 1
        return {"errcode": 0, "access_token": next(tokens), "expires_in": 7200}

    def fake_post(url, *, params, json, timeout):
        calls["post_tokens"].append(params["access_token"])
        if params["access_token"] == "expired-token":
            return {"errcode": 40014, "errmsg": "invalid access token"}
        return {"errcode": 0, "errmsg": "ok", "msgid": "msg-after-refresh"}

    client = WeComCustomerGroupClient(
        corp_id="corp_001",
        secret="secret_001",
        http_get=fake_get,
        http_post=fake_post,
    )

    result = client.create_group_message_task({"sender": "owner_001"})

    assert result["msgid"] == "msg-after-refresh"
    assert calls == {
        "get": 2,
        "post_tokens": ["expired-token", "fresh-token"],
    }


def test_customer_group_client_retries_invalid_token_only_once() -> None:
    calls = {"get": 0, "post": 0}

    def fake_get(url, *, params, timeout):
        calls["get"] += 1
        return {"errcode": 0, "access_token": f"token-{calls['get']}", "expires_in": 7200}

    def fake_post(url, *, params, json, timeout):
        calls["post"] += 1
        return {"errcode": 42001, "errmsg": "token expired"}

    client = WeComCustomerGroupClient(
        corp_id="corp_001",
        secret="secret_001",
        http_get=fake_get,
        http_post=fake_post,
    )

    assert client.create_group_message_task({"sender": "owner_001"})["errcode"] == 42001
    assert calls == {"get": 2, "post": 2}


def test_customer_group_client_preserves_http_429_rate_limit_signal() -> None:
    client = WeComCustomerGroupClient(
        corp_id="corp_001",
        secret="secret_001",
        http_get=lambda *args, **kwargs: {
            "errcode": 0,
            "access_token": "token",
            "expires_in": 7200,
        },
        http_post=lambda *args, **kwargs: FakeResponse(
            {"errcode": 0, "errmsg": "too many requests"},
            status_code=429,
            headers={"Retry-After": "3"},
        ),
    )

    with pytest.raises(WeComCustomerGroupClientError) as exc:
        client.create_group_message_task({"sender": "owner_001"})

    assert exc.value.error_code == "rate_limited"
    assert exc.value.status_code == 429
    assert exc.value.retry_after_seconds == 3.0
