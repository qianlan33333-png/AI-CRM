from __future__ import annotations

import base64

from fastapi.testclient import TestClient
import jwt

from aicrm_next.extensions.ai.ai_assist import api as ai_assist_api
from aicrm_next.main import create_app
from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from tests.admin_auth_test_helpers import TEST_JWT_KEY, install_admin_auth_service


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_admin_auth_service(client)
    return client


def _campaign_credentials(client: TestClient) -> tuple[str, str]:
    service = client.app.state.auth_client_service
    issued = service.create_client(
        client_id="pytest-campaign-agent",
        principal_id="api_client:pytest-campaign-agent",
        principal_type=PrincipalType.API_CLIENT,
        purpose="campaign_agent",
        display_name="Pytest Campaign Agent",
        audiences=("external_integration",),
        scopes=("read", "write"),
        capabilities=(
            "campaign_draft_create",
            "campaign_status_read",
            "customer_read_limited",
            "customer_resolve_read",
            "material_create",
            "material_read",
        ),
    )
    return issued.client.client_id, issued.client_secret


def _token(client: TestClient, client_id: str, client_secret: str) -> str:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = client.post(
        "/oauth/token",
        headers={"Authorization": f"Basic {basic}"},
        data={
            "grant_type": "client_credentials",
            "audience": "external_integration",
            "scope": "read write",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["token_type"] == "Bearer"
    return response.json()["access_token"]


def test_client_credentials_issues_short_lived_signed_jwt_and_no_extra_oauth_surface(monkeypatch) -> None:
    client = _client(monkeypatch)
    client_id, secret = _campaign_credentials(client)

    token = _token(client, client_id, secret)
    claims = jwt.decode(
        token,
        TEST_JWT_KEY,
        algorithms=["HS256"],
        audience="external_integration",
        issuer="https://testserver/oauth",
    )

    assert claims["client_id"] == client_id
    assert claims["principal_type"] == "api_client"
    assert claims["auth_version"] == 1
    assert claims["exp"] - claims["iat"] == 1800
    assert set(claims["scope"].split()) == {"read", "write"}
    registered_paths = {getattr(route, "path", "") for route in client.app.routes}
    assert "/.well-known/openid-configuration" not in registered_paths
    assert "/oauth/jwks" not in registered_paths
    assert "/oauth/introspect" not in registered_paths
    assert "/oauth/revoke" not in registered_paths
    assert "/oauth/authorize" not in registered_paths


def test_campaign_agent_can_create_draft_and_read_status_only(monkeypatch) -> None:
    client = _client(monkeypatch)
    client_id, secret = _campaign_credentials(client)
    token = _token(client, client_id, secret)
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(
        ai_assist_api,
        "create_external_campaigns_response",
        lambda payload: {"ok": True, "campaign_code": payload.get("campaign_code") or "draft-1"},
    )
    monkeypatch.setattr(
        ai_assist_api,
        "get_external_campaign_status_response",
        lambda campaign_code: {"ok": True, "campaign_code": campaign_code, "run_status": "draft"},
    )

    created = client.post(
        "/api/ai-assist/external/campaigns",
        headers=headers,
        json={"campaign_code": "draft-1"},
    )
    status = client.get("/api/ai-assist/external/campaigns/draft-1", headers=headers)

    assert created.status_code == 200
    assert created.json()["campaign_code"] == "draft-1"
    assert status.status_code == 200
    assert status.json()["run_status"] == "draft"


def test_campaign_agent_is_exactly_forbidden_from_approve_start_and_direct_send(monkeypatch) -> None:
    client = _client(monkeypatch)
    client_id, secret = _campaign_credentials(client)
    headers = {"Authorization": f"Bearer {_token(client, client_id, secret)}"}

    attempts = (
        ("post", "/api/admin/cloud-orchestrator/campaigns/draft-1/approve", {}),
        ("post", "/api/admin/cloud-orchestrator/campaigns/draft-1/start", {}),
        ("post", "/api/internal/direct-send/wecom-private", {"external_userid": "wx-test"}),
        ("post", "/api/admin/direct-send/wecom-private", {"external_userid": "wx-test"}),
    )

    for method, path, payload in attempts:
        response = getattr(client, method)(path, headers=headers, json=payload)
        assert response.status_code == 403, (path, response.text)
