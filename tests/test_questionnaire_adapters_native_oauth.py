from __future__ import annotations

from pathlib import Path

import pytest

from aicrm_next.channels.integration_gateway.audit import reset_audit_events
from aicrm_next.channels.integration_gateway.idempotency import reset_idempotency_store
from aicrm_next.channels.integration_gateway.questionnaire_adapters import WeChatOAuthAdapter
from aicrm_next.channels.integration_gateway.wechat_oauth_client import WeChatOAuthClientError


@pytest.fixture(autouse=True)
def reset_gateway_state(monkeypatch: pytest.MonkeyPatch):
    reset_audit_events()
    reset_idempotency_store()
    for key in (
        "AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH",
        "WECHAT_MP_APP_ID",
        "WECHAT_MP_APP_SECRET",
        "WECHAT_MP_OAUTH_SCOPE",
        "AICRM_NEXT_WECHAT_OAUTH_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    reset_audit_events()
    reset_idempotency_store()


def _fail_client_factory():
    raise AssertionError("oauth client should not be called")


class FakeOAuthClient:
    def __init__(self, *, exchange_payload=None, userinfo_payload=None, error: Exception | None = None) -> None:
        self.exchange_payload = exchange_payload or {}
        self.userinfo_payload = userinfo_payload or {}
        self.error = error
        self.exchange_calls: list[dict] = []
        self.userinfo_calls: list[dict] = []

    def exchange_code(self, *, app_id: str, app_secret: str, code: str):
        self.exchange_calls.append({"app_id": app_id, "app_secret": app_secret, "code": code})
        if self.error:
            raise self.error
        return self.exchange_payload

    def fetch_userinfo(self, *, access_token: str, openid: str):
        self.userinfo_calls.append({"access_token": access_token, "openid": openid})
        return self.userinfo_payload


def test_wechat_oauth_adapter_fake_resolve_does_not_call_client() -> None:
    adapter = WeChatOAuthAdapter(mode="fake", oauth_client_factory=_fail_client_factory)

    result = adapter.resolve_oauth_identity(state="q1", code="code")

    assert result["ok"] is True
    assert result["result"]["source_status"] == "fake"
    assert result["side_effect_executed"] is False


def test_wechat_oauth_adapter_production_guard_blocks_without_flag() -> None:
    adapter = WeChatOAuthAdapter(mode="production", oauth_client_factory=_fail_client_factory)

    result = adapter.resolve_oauth_identity(code="code")

    assert result["ok"] is False
    assert result["error_code"] == "production_guard_failed"
    assert result["side_effect_executed"] is False


def test_wechat_oauth_adapter_production_missing_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", "1")
    adapter = WeChatOAuthAdapter(mode="production", oauth_client_factory=_fail_client_factory)

    result = adapter.resolve_oauth_identity(code="")

    assert result["ok"] is False
    assert result["error_code"] == "oauth_code_required"
    assert result["side_effect_executed"] is False


def test_wechat_oauth_adapter_production_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", "1")
    adapter = WeChatOAuthAdapter(mode="production", oauth_client_factory=_fail_client_factory)

    result = adapter.resolve_oauth_identity(code="code")

    assert result["ok"] is False
    assert result["error_code"] == "wechat_oauth_not_configured"
    assert result["side_effect_executed"] is False


def test_wechat_oauth_adapter_production_exchange_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", "1")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx_app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "secret_value")
    client = FakeOAuthClient(exchange_payload={"openid": "openid_001", "unionid": "union_001", "access_token": "token_001"})
    adapter = WeChatOAuthAdapter(mode="production", oauth_client_factory=lambda: client)

    result = adapter.resolve_oauth_identity(code="code_001", state="slug", redirect="/s/slug")

    assert result["ok"] is True
    assert result["result"]["openid"] == "openid_001"
    assert result["result"]["unionid"] == "union_001"
    assert result["result"]["source_status"] == "production"
    assert result["side_effect_executed"] is True
    assert result["target"]["code_hash"]
    assert "code_001" not in str(result["target"])
    assert client.exchange_calls == [{"app_id": "wx_app", "app_secret": "secret_value", "code": "code_001"}]
    assert client.userinfo_calls == []


def test_wechat_oauth_adapter_fetches_userinfo_when_unionid_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", "1")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx_app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "secret_value")
    monkeypatch.setenv("WECHAT_MP_OAUTH_SCOPE", "snsapi_userinfo")
    client = FakeOAuthClient(
        exchange_payload={"openid": "openid_001", "access_token": "token_001"},
        userinfo_payload={"openid": "openid_001", "unionid": "union_from_userinfo"},
    )
    adapter = WeChatOAuthAdapter(mode="production", oauth_client_factory=lambda: client)

    result = adapter.resolve_oauth_identity(code="code_001", state="slug")

    assert result["ok"] is True
    assert result["result"]["unionid"] == "union_from_userinfo"
    assert client.userinfo_calls == [{"access_token": "token_001", "openid": "openid_001"}]


def test_wechat_oauth_adapter_exchange_wechat_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", "1")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx_app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "secret_value")
    client = FakeOAuthClient(exchange_payload={"errcode": 40029, "errmsg": "invalid code"})
    adapter = WeChatOAuthAdapter(mode="production", oauth_client_factory=lambda: client)

    result = adapter.resolve_oauth_identity(code="code_001", state="slug")

    assert result["ok"] is False
    assert result["error_code"] == "wechat_oauth_exchange_failed"
    assert result["side_effect_executed"] is True


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (WeChatOAuthClientError("native client failed", error_code="wechat_oauth_http_error"), "native client failed"),
        (RuntimeError("raw code_001 secret_value token_001"), "WeChat OAuth exchange failed"),
    ],
)
def test_wechat_oauth_adapter_client_exception_is_controlled(monkeypatch: pytest.MonkeyPatch, error: Exception, expected_message: str) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENABLE_REAL_WECHAT_OAUTH", "1")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx_app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "secret_value")
    client = FakeOAuthClient(error=error)
    adapter = WeChatOAuthAdapter(mode="production", oauth_client_factory=lambda: client)

    result = adapter.resolve_oauth_identity(code="code_001", state="slug")

    assert result["ok"] is False
    assert result["error_code"] == "wechat_oauth_exchange_failed"
    assert result["error_message"] == expected_message
    assert "code_001" not in result["error_message"]
    assert "secret_value" not in result["error_message"]
    assert "token_001" not in result["error_message"]


def test_questionnaire_adapters_runtime_has_no_legacy_oauth_import() -> None:
    source = Path("aicrm_next/channels/integration_gateway/questionnaire_adapters.py").read_text(encoding="utf-8")

    assert "wecom_ability" + "_service" not in source
    assert "wechat_oauth.exchange_wechat_oauth_code" not in source
    assert "legacy_flask_facade" not in source
