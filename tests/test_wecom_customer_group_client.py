from __future__ import annotations

import pytest

from aicrm_next.channels.integration_gateway.wecom_customer_group_client import (
    WeComCustomerGroupClient,
    WeComCustomerGroupClientError,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

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
