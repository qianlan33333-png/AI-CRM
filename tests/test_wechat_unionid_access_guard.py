from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.identity_contact.wechat_unionid_guard import evaluate_wechat_unionid_access
from aicrm_next.main import create_app
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.extensions.forms.questionnaire.result_access import RESULT_GRANT_COOKIE_NAME, result_grant_cookie_path
from aicrm_next.extensions.forms.questionnaire.h5_write import reset_questionnaire_h5_write_fixture_state
from aicrm_next.extensions.forms.questionnaire.repo import reset_questionnaire_fixture_state


WECHAT_UA = "Mozilla/5.0 MicroMessenger/8.0"


def test_shared_guard_requires_unionid_instead_of_openid() -> None:
    openid_only = evaluate_wechat_unionid_access(
        {"openid": "openid_only"},
        is_wechat_browser=True,
        oauth_start_url="/oauth/start",
    )
    canonical = evaluate_wechat_unionid_access(
        {"openid": "openid_ready", "unionid": "unionid_ready"},
        is_wechat_browser=True,
        oauth_start_url="/oauth/start",
    )
    outside_wechat = evaluate_wechat_unionid_access(
        {},
        is_wechat_browser=False,
        oauth_start_url="/oauth/start",
    )

    assert openid_only.allowed is False
    assert openid_only.error == "unionid_oauth_required"
    assert openid_only.status_code == 401
    assert canonical.allowed is True
    assert canonical.identity["unionid"] == "unionid_ready"
    assert outside_wechat.error == "wechat_browser_required"
    assert outside_wechat.status_code == 403


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("SECRET_KEY", "wechat-unionid-access-guard-test")
    reset_questionnaire_fixture_state()
    reset_questionnaire_h5_write_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def test_openid_only_signed_session_cannot_use_protected_h5_capabilities(client: TestClient) -> None:
    client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob({"openid": "openid_only_session"}),
    )
    headers = {"User-Agent": WECHAT_UA}

    responses = [
        client.get("/api/h5/questionnaires/hxc-activation-v1", headers=headers),
        client.post(
            "/api/h5/questionnaires/hxc-activation-v1/submit",
            json={"answers": {"q_activation": "activated"}},
            headers=headers,
        ),
        client.post("/api/h5/coupons/coupon-any/claim", headers=headers),
        client.get("/api/h5/service-period-products/period-any", headers=headers),
        client.post(
            "/api/h5/wechat-pay/jsapi/orders",
            json={"product_code": "test-product"},
            headers=headers,
        ),
    ]

    for response in responses:
        assert response.status_code == 401, response.text
        payload = response.json()
        assert payload["error"] == "unionid_oauth_required"
        assert payload["oauth_start_url"].startswith("/api/h5/wechat-pay/oauth/start?")


def test_questionnaire_query_unionid_cannot_bypass_signed_session_guard(client: TestClient) -> None:
    response = client.get(
        "/s/hxc-activation-v1",
        params={"openid": "openid_forged", "unionid": "unionid_forged"},
        headers={"User-Agent": WECHAT_UA},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/api/h5/wechat-pay/oauth/start?return_url=%2Fs%2Fhxc-activation-v1"
    assert "unionid_forged" not in response.headers["location"]


def test_unionid_signed_session_enters_questionnaire_without_oauth_loop(client: TestClient) -> None:
    client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob({"openid": "openid_ready", "unionid": "unionid_ready"}),
    )

    response = client.get(
        "/s/hxc-activation-v1",
        headers={"User-Agent": WECHAT_UA},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert '"mode": "questionnaire"' in response.text
    assert '"is_authorized": true' in response.text
    assert "questionnaire-form" in response.text
    assert "unionid_ready" not in response.text


def test_questionnaire_result_grant_does_not_replace_unionid_session(client: TestClient) -> None:
    authorize_payload = {"openid": "openid_result", "unionid": "unionid_result"}
    client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob(authorize_payload),
    )
    submit = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={"answers": {"q_activation": "activated"}},
        headers={"User-Agent": WECHAT_UA},
    )
    assert submit.status_code == 200, submit.text
    result_grant = client.cookies.get(RESULT_GRANT_COOKIE_NAME)
    assert result_grant

    copied_grant_client = TestClient(client.app, raise_server_exceptions=False)
    copied_grant_client.cookies.set(
        RESULT_GRANT_COOKIE_NAME,
        result_grant,
        path=result_grant_cookie_path("hxc-activation-v1"),
    )
    response = copied_grant_client.get(
        "/api/h5/questionnaires/hxc-activation-v1/result",
        headers={"User-Agent": WECHAT_UA},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "unionid_oauth_required"
