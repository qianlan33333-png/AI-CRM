from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app
from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from aicrm_next.platform.platform_foundation.auth_platform.credentials import hash_client_secret, issue_client_secret
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.profiles import DIRECT_EXTERNAL_API_KEY_CLIENT_ID
from tests.admin_auth_test_helpers import access_token_headers, install_access_token


def _client(monkeypatch, *, authorized: bool = True) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "external-orders-api")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)
    token = install_access_token(
        client,
        audience="external_integration",
        capabilities=("external_read",),
        scopes=("read",),
        client_id="pytest-external-orders",
        purpose="external_agent",
    )
    if authorized:
        client.headers.update(access_token_headers(token))
    return client


def _headers() -> dict[str, str]:
    return {}


def test_external_orders_requires_registered_client_access_token(monkeypatch) -> None:
    client = _client(monkeypatch, authorized=False)
    missing = client.get("/api/external/orders")
    assert missing.status_code == 401
    assert missing.json()["error"] == "access_token_required"

    invalid = client.get("/api/external/orders", headers={"Authorization": "Bearer wrong-token"})
    assert invalid.status_code == 401
    assert invalid.json()["error"] == "invalid_access_token"


def test_external_orders_accepts_single_direct_read_key_and_rejects_external_writes(monkeypatch) -> None:
    client = _client(monkeypatch, authorized=False)
    api_key = issue_client_secret()
    repository = client.app.state.auth_client_service.repository
    repository.api_clients[DIRECT_EXTERNAL_API_KEY_CLIENT_ID] = ApiClientRecord(
        client_id=DIRECT_EXTERNAL_API_KEY_CLIENT_ID,
        principal_id=f"api_client:{DIRECT_EXTERNAL_API_KEY_CLIENT_ID}",
        principal_type=PrincipalType.API_CLIENT,
        purpose="external_agent",
        display_name="CRM 开放 API Key",
        secret_hash=hash_client_secret(api_key),
        audiences=("external_integration",),
        scopes=("read",),
        capabilities=("external_read",),
        allowed_cidrs=(),
        corp_id="corp-pytest",
        owner_scope={},
        auth_version=1,
        token_ttl_seconds=1800,
        enabled=True,
    )
    headers = {"Authorization": f"Bearer {api_key}"}

    readable = client.get("/api/external/orders?limit=1", headers=headers)
    assert readable.status_code == 200

    write_rejected = client.post(
        "/api/external/ai-audience/templates/apply",
        headers=headers,
        json={},
    )
    assert write_rejected.status_code == 403
    assert write_rejected.json()["error"] == "scope_or_capability_required"


def test_external_orders_list_returns_lightweight_contract_and_excludes_unpaid_for_paid_range(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/external/orders?provider=all&paid_from=1779235200&created_from=1779235200&limit=100",
        headers=_headers(),
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["source_status"] == "external_orders"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert [item["order_no"] for item in payload["items"]] == ["order_masked_001"]
    item = payload["items"][0]
    assert item["provider"] == "wechat"
    assert item["transaction_id"] == "transaction_masked_001"
    assert item["product_code"] == "course_masked_001"
    assert "product_name" not in item
    assert item["payment_status"] == "paid"
    assert item["is_paid"] is True
    assert item["is_refunded"] is False
    assert item["detail_url"] == "/api/external/orders/order_masked_001?provider=wechat"


def test_external_orders_supports_paid_and_refund_filters(monkeypatch) -> None:
    client = _client(monkeypatch)

    unpaid = client.get("/api/external/orders?is_paid=false", headers=_headers()).json()
    assert {item["order_no"] for item in unpaid["items"]} == {"order_fake_0002", "order_fake_0003"}

    refunded = client.get("/api/external/orders?is_refunded=true", headers=_headers()).json()
    assert refunded["items"] == []


def test_external_orders_rejects_millisecond_timestamps(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/external/orders?paid_from=1779235200000",
        headers=_headers(),
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"


def test_external_orders_cursor_paginates_with_opaque_token(monkeypatch) -> None:
    client = _client(monkeypatch)
    first = client.get("/api/external/orders?limit=1", headers=_headers()).json()

    assert first["items"]
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert "offset" not in first

    second = client.get(f"/api/external/orders?limit=1&cursor={first['next_cursor']}", headers=_headers()).json()
    assert second["items"]
    assert second["items"][0]["order_no"] != first["items"][0]["order_no"]


def test_external_order_detail_reuses_unified_detail_projection(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/external/orders/order_masked_001?provider=wechat",
        headers=_headers(),
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["source_status"] == "external_order_detail"
    order = payload["order"]
    assert order["order_no"] == "order_masked_001"
    assert order["transaction_id"] == "transaction_masked_001"
    assert "product_name" not in order
    assert "refund_status" in order
    assert order["refunded_amount_total"] == 0
    assert "refundable_amount_total" in order
    assert "callback_summary" in order
    assert "timeline" in order


def test_external_user_basic_requires_bearer_token(monkeypatch) -> None:
    client = _client(monkeypatch, authorized=False)

    missing = client.get("/api/external/users/resolve?unionid=unionid_001")
    assert missing.status_code == 401
    assert missing.json()["error"] == "access_token_required"

    invalid = client.get(
        "/api/external/users/resolve?unionid=unionid_001",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["error"] == "invalid_access_token"


def test_external_user_basic_requires_identity_key(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/external/users/resolve", headers=_headers())

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"


def test_external_user_basic_resolves_required_fields_by_unionid(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/external/users/resolve?unionid=unionid_001",
        headers=_headers(),
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["source_status"] == "external_user_basic"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    user = payload["user"]
    assert user["unionid"] == "unionid_001"
    assert user["mobile"] == "13800138000"
    assert user["customer_name"] == "张小蓝"
    assert user["external_userid"] == "wx_ext_001"
    assert user["matched_by"] == "unionid"
