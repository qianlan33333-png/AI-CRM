from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "yuanqi-official-site-test")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_yuanqi_official_site_is_public_next_owned_and_compliant(monkeypatch) -> None:
    response = _client(monkeypatch).get("/")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert "元气女性成长联盟" in response.text
    assert "武汉闪闪少女文化传播有限公司" in response.text
    assert "我们提供的服务" in response.text
    assert "服务流程" in response.text
    assert "用户权益与合规说明" in response.text
    assert "不提供医疗诊断、心理治疗或投资理财服务" in response.text
    assert "不对个人收入、经营收益或特定结果作出承诺" in response.text
    assert "¥" not in response.text


def test_yuanqi_official_site_supports_head(monkeypatch) -> None:
    response = _client(monkeypatch).head("/")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"].startswith("text/html")
