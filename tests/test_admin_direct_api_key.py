from __future__ import annotations

import pytest

from aicrm_next.platform.platform_foundation.auth_platform.profiles import DIRECT_EXTERNAL_API_KEY_CLIENT_ID
from aicrm_next.platform.platform_foundation.auth_platform.service import AuthError

from tests.admin_auth_test_helpers import (
    install_admin_action_tokens,
    install_admin_auth_service,
    install_admin_session,
)


GENERATE_ROUTE = "/api/admin/config/api-key/generate"
ROTATE_ROUTE = "/api/admin/config/api-key/rotate"
ENABLED_ROUTE = "/api/admin/config/api-key/enabled"


@pytest.fixture
def direct_api_key_runtime(client):
    _session_service, repository = install_admin_auth_service(client)
    return client.app.state.auth_client_service, repository


def _token(client, method: str, route: str, *, roles: tuple[str, ...] = ("super_admin",)) -> str:
    return install_admin_action_tokens(client, (method, route), roles=roles)[(method, route)]


def _headers(token: str) -> dict[str, str]:
    return {"X-Admin-Action-Token": token}


def _generate(client):
    return client.post(
        GENERATE_ROUTE,
        headers=_headers(_token(client, "POST", GENERATE_ROUTE)),
        json={"confirm": True},
    )


def test_generate_direct_key_is_once_only_hashed_and_read_only(client, direct_api_key_runtime) -> None:
    service, repository = direct_api_key_runtime
    created = _generate(client)

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    api_key = body["api_key"]
    assert api_key.startswith("aics_")
    assert body["api_key_status"]["enabled"] is True
    assert "secret_hash" not in created.text
    assert api_key not in str(repository.api_client_audits)

    stored = repository.api_clients[DIRECT_EXTERNAL_API_KEY_CLIENT_ID]
    assert stored.secret_hash != api_key
    assert stored.scopes == ("read",)
    assert stored.capabilities == ("external_read",)
    listed_client_ids = {
        row["client_id"] for row in client.get("/api/admin/config/api-clients").json()["api_clients"]["rows"]
    }
    assert DIRECT_EXTERNAL_API_KEY_CLIENT_ID not in listed_client_ids
    status_response = client.get("/api/admin/config/api-key")
    assert status_response.status_code == 200
    assert api_key not in status_response.text
    assert "secret_hash" not in status_response.text

    context = service.verify_access_token(
        api_key,
        audience="external_integration",
        client_purpose="external_agent",
        request_id="req-direct-key",
    )
    assert context.client_id == DIRECT_EXTERNAL_API_KEY_CLIENT_ID
    assert context.request_id == "req-direct-key"
    assert context.permits(capability="external_read", scope="read")
    assert not context.permits(capability="external_write", scope="write")

    with pytest.raises(AuthError, match="client_purpose_forbidden"):
        service.verify_access_token(api_key, audience="external_integration", client_purpose="mcp")
    with pytest.raises(AuthError, match="invalid_target"):
        service.verify_access_token(api_key, audience="internal_worker")

    duplicate = _generate(client)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "direct_api_key_already_configured"


def test_rotate_immediately_invalidates_old_key_and_disable_invalidates_new_key(client, direct_api_key_runtime) -> None:
    service, repository = direct_api_key_runtime
    old_key = _generate(client).json()["api_key"]

    rotated = client.post(
        ROTATE_ROUTE,
        headers=_headers(_token(client, "POST", ROTATE_ROUTE)),
        json={"confirm": True},
    )
    assert rotated.status_code == 200
    assert rotated.headers["cache-control"] == "no-store"
    new_key = rotated.json()["api_key"]
    assert new_key != old_key
    assert rotated.json()["api_key_status"]["auth_version"] == 2

    with pytest.raises(AuthError, match="invalid_access_token"):
        service.verify_access_token(old_key, audience="external_integration", client_purpose="external_agent")
    assert service.verify_access_token(
        new_key,
        audience="external_integration",
        client_purpose="external_agent",
    ).client_id == DIRECT_EXTERNAL_API_KEY_CLIENT_ID

    disabled = client.put(
        ENABLED_ROUTE,
        headers=_headers(_token(client, "PUT", ENABLED_ROUTE)),
        json={"enabled": False, "confirm": True},
    )
    assert disabled.status_code == 200
    assert disabled.json()["api_key_status"]["enabled"] is False
    with pytest.raises(AuthError, match="client_disabled"):
        service.verify_access_token(new_key, audience="external_integration", client_purpose="external_agent")

    assert [row.action_type for row in repository.api_client_audits] == [
        "direct_external_api_key_generated",
        "direct_external_api_key_rotated",
        "direct_external_api_key_disabled",
    ]
    assert old_key not in str(repository.api_client_audits)
    assert new_key not in str(repository.api_client_audits)


def test_read_permissions_unknown_fields_action_tokens_and_audit_rollback(client, direct_api_key_runtime) -> None:
    _service, repository = direct_api_key_runtime
    install_admin_session(client, "config_admin")
    page = client.get("/admin/config/api-key")
    assert page.status_code == 200
    assert "生成并启用 API Key" in page.text
    assert client.get("/api/admin/config/api-key").json()["api_key_status"]["configured"] is False

    generated = client.post(
        GENERATE_ROUTE,
        headers=_headers(_token(client, "POST", GENERATE_ROUTE, roles=("config_admin",))),
        json={"confirm": True},
    )
    assert generated.status_code == 201

    install_admin_session(client, "viewer")
    rejected = client.post(
        ROTATE_ROUTE,
        json={"confirm": True},
    )
    assert rejected.status_code == 403

    install_admin_session(client, "super_admin")
    unknown = client.post(
        ROTATE_ROUTE,
        headers=_headers(_token(client, "POST", ROTATE_ROUTE)),
        json={"confirm": True, "scope": "write"},
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"].startswith("unknown_fields:")

    wrong_route_token = _token(client, "PUT", ENABLED_ROUTE)
    cross_route = client.post(
        ROTATE_ROUTE,
        headers=_headers(wrong_route_token),
        json={"confirm": True},
    )
    assert cross_route.status_code in {400, 401, 403}
    assert repository.api_clients[DIRECT_EXTERNAL_API_KEY_CLIENT_ID].auth_version == 1

    repository.fail_api_client_audit = True
    with pytest.raises(RuntimeError, match="simulated_api_client_audit_failure"):
        client.post(
            ROTATE_ROUTE,
            headers=_headers(_token(client, "POST", ROTATE_ROUTE)),
            json={"confirm": True},
        )
    assert repository.api_clients[DIRECT_EXTERNAL_API_KEY_CLIENT_ID].auth_version == 1
