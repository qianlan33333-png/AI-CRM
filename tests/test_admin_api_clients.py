from __future__ import annotations

import pytest

from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from aicrm_next.platform.platform_foundation.auth_platform.credentials import hash_client_secret, issue_client_secret
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.service import AuthError

from tests.admin_auth_test_helpers import (
    install_admin_action_tokens,
    install_admin_auth_service,
    install_admin_session,
)


CREATE_ROUTE = "/api/admin/config/api-clients"
CLIENT_ROUTE = "/api/admin/config/api-clients/{client_id}"
ACTIVATE_ROUTE = "/api/admin/config/api-clients/{client_id}/activate"
ROTATE_ROUTE = "/api/admin/config/api-clients/{client_id}/rotate-secret"
ENABLED_ROUTE = "/api/admin/config/api-clients/{client_id}/enabled"


@pytest.fixture
def api_client_runtime(client):
    _session_service, repository = install_admin_auth_service(client)
    service = client.app.state.auth_client_service
    return service, repository, repository.api_client_audits


def _token(client, method: str, route: str, *, roles: tuple[str, ...] = ("super_admin",)) -> str:
    return install_admin_action_tokens(client, (method, route), roles=roles)[(method, route)]


def _headers(token: str) -> dict[str, str]:
    return {"X-Admin-Action-Token": token}


def _create(client, *, client_id: str = "prod-operator-api", client_type: str = "external_api"):
    token = _token(client, "POST", CREATE_ROUTE)
    return client.post(
        CREATE_ROUTE,
        headers=_headers(token),
        json={
            "display_name": "运营 Agent",
            "client_id": client_id,
            "client_type": client_type,
            "token_ttl_minutes": 30,
            "allowed_cidrs": ["203.0.113.7/32"],
            "confirm": True,
        },
    )


def _seed_client(
    repository,
    *,
    client_id: str,
    secret: str,
    purpose: str,
    scopes: tuple[str, ...],
    capabilities: tuple[str, ...],
    enabled: bool = False,
) -> None:
    repository.api_clients[client_id] = ApiClientRecord(
        client_id=client_id,
        principal_id=f"api_client:{client_id}",
        principal_type=PrincipalType.API_CLIENT,
        purpose=purpose,
        display_name=client_id,
        secret_hash=hash_client_secret(secret),
        audiences=("external_integration",),
        scopes=scopes,
        capabilities=capabilities,
        allowed_cidrs=(),
        corp_id="corp-pytest",
        owner_scope={},
        auth_version=1,
        token_ttl_seconds=1800,
        enabled=enabled,
    )


def test_super_admin_create_self_check_rotate_and_disable_invalidates_tokens(client, api_client_runtime) -> None:
    service, repository, audit = api_client_runtime

    created = _create(client)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    created_body = created.json()
    secret = created_body["client_secret"]
    assert secret
    assert created_body["client"]["enabled"] is False
    assert created_body["client"]["credential_hint_available"] is True
    assert created_body["client"]["credential_hint"].startswith(secret[:9])
    assert created_body["client"]["credential_hint"].endswith(secret[-4:])
    assert secret not in created_body["client"]["credential_hint"]
    assert repository.api_clients["prod-operator-api"].enabled is False
    assert "secret_hash" not in created.text

    activate_token = _token(client, "POST", ACTIVATE_ROUTE)
    activated = client.post(
        "/api/admin/config/api-clients/prod-operator-api/activate",
        headers=_headers(activate_token),
        json={"client_secret": secret, "copied_confirmed": True, "confirm": True},
    )
    assert activated.status_code == 200
    assert activated.json()["client"]["enabled"] is True

    issued = service.issue_client_credentials_token(
        client_id="prod-operator-api",
        client_secret=secret,
        audience="external_integration",
        requested_scopes=("read",),
        source_ip="203.0.113.7",
    )
    assert service.verify_access_token(
        issued.access_token,
        audience="external_integration",
        source_ip="203.0.113.7",
    ).client_id == "prod-operator-api"

    rotate_token = _token(client, "POST", ROTATE_ROUTE)
    rotated = client.post(
        "/api/admin/config/api-clients/prod-operator-api/rotate-secret",
        headers=_headers(rotate_token),
        json={"confirm": True},
    )
    assert rotated.status_code == 200
    assert rotated.headers["cache-control"] == "no-store"
    assert rotated.json()["client"]["enabled"] is False
    assert rotated.json()["client_secret"] != secret
    assert rotated.json()["client"]["credential_hint"].endswith(rotated.json()["client_secret"][-4:])
    assert not rotated.json()["client"]["credential_hint"].endswith(secret[-4:])
    with pytest.raises(AuthError, match="client_disabled"):
        service.verify_access_token(
            issued.access_token,
            audience="external_integration",
            source_ip="203.0.113.7",
        )

    new_secret = rotated.json()["client_secret"]
    reactivate_token = _token(client, "POST", ACTIVATE_ROUTE)
    assert client.post(
        "/api/admin/config/api-clients/prod-operator-api/activate",
        headers=_headers(reactivate_token),
        json={"client_secret": new_secret, "copied_confirmed": True, "confirm": True},
    ).status_code == 200

    disable_token = _token(client, "PUT", ENABLED_ROUTE)
    disabled = client.put(
        "/api/admin/config/api-clients/prod-operator-api/enabled",
        headers=_headers(disable_token),
        json={"enabled": False, "confirm": True},
    )
    assert disabled.status_code == 200
    assert disabled.json()["client"]["enabled"] is False
    assert [row.action_type for row in audit] == [
        "api_client_created",
        "api_client_activated",
        "api_client_secret_rotated",
        "api_client_activated",
        "api_client_disabled",
    ]
    assert secret not in str(audit)
    assert new_secret not in str(audit)

    detail = client.get("/api/admin/config/api-clients/prod-operator-api")
    assert detail.status_code == 200
    assert detail.json()["client"]["credential_hint"] == rotated.json()["client"]["credential_hint"]
    assert new_secret not in detail.text


def test_fixed_templates_validation_and_existing_readonly_client_are_preserved(client, api_client_runtime) -> None:
    _service, repository, _audit = api_client_runtime
    legacy_secret = issue_client_secret()
    _seed_client(
        repository,
        client_id="aicrm-external-reader-qianlan",
        secret=legacy_secret,
        purpose="external_agent_personal_read",
        scopes=("read",),
        capabilities=("external_read",),
        enabled=True,
    )
    install_admin_session(client, "config_admin")
    listed = client.get(CREATE_ROUTE)
    assert listed.status_code == 200
    row = next(item for item in listed.json()["api_clients"]["rows"] if item["client_id"] == "aicrm-external-reader-qianlan")
    assert row["scopes"] == ["read"]
    assert row["capabilities"] == ["external_read"]
    assert row["permission_label"] == "历史权限（保持不变）"

    install_admin_session(client, "super_admin")
    token = _token(client, "POST", CREATE_ROUTE)
    invalid = client.post(
        CREATE_ROUTE,
        headers=_headers(token),
        json={
            "display_name": "Bad",
            "client_id": "Bad Client",
            "client_type": "custom",
            "token_ttl_minutes": 10,
            "allowed_cidrs": ["not-cidr"],
            "confirm": True,
        },
    )
    assert invalid.status_code == 400
    assert len(repository.api_clients) == 1


def test_config_admin_can_read_but_only_super_admin_can_write(client, api_client_runtime) -> None:
    install_admin_session(client, "config_admin")
    assert client.get("/admin/config/api-clients").status_code == 200
    assert client.get(CREATE_ROUTE).status_code == 200
    rejected = client.post(
        CREATE_ROUTE,
        json={
            "display_name": "配置管理员无权创建",
            "client_id": "config-admin-client",
            "client_type": "external_api",
            "token_ttl_minutes": 30,
            "confirm": True,
        },
    )
    assert rejected.status_code == 403


def test_unknown_fields_route_bound_token_and_active_update_guards(client, api_client_runtime) -> None:
    _service, repository, _audit = api_client_runtime
    created = _create(client, client_id="guarded-client")
    secret = created.json()["client_secret"]

    create_token = _token(client, "POST", CREATE_ROUTE)
    unknown = client.post(
        CREATE_ROUTE,
        headers=_headers(create_token),
        json={
            "display_name": "未知字段",
            "client_id": "unknown-field-client",
            "client_type": "external_api",
            "token_ttl_minutes": 30,
            "scope": "admin",
            "confirm": True,
        },
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"].startswith("unknown_fields:")

    activate_token = _token(client, "POST", ACTIVATE_ROUTE)
    assert client.post(
        "/api/admin/config/api-clients/guarded-client/activate",
        headers=_headers(activate_token),
        json={"client_secret": secret, "copied_confirmed": True, "confirm": True},
    ).status_code == 200

    wrong_token = _token(client, "PUT", ENABLED_ROUTE)
    cross_route = client.post(
        "/api/admin/config/api-clients/guarded-client/rotate-secret",
        headers=_headers(wrong_token),
        json={"confirm": True},
    )
    assert cross_route.status_code in {400, 401, 403}
    assert repository.api_clients["guarded-client"].enabled is True

    update_token = _token(client, "PUT", CLIENT_ROUTE)
    active_update = client.put(
        "/api/admin/config/api-clients/guarded-client",
        headers=_headers(update_token),
        json={
            "display_name": "不能直接修改",
            "token_ttl_minutes": 60,
            "allowed_cidrs": [],
            "confirm": True,
        },
    )
    assert active_update.status_code == 409
    assert active_update.json()["error"] == "active_client_update_requires_disable"


def test_system_managed_clients_are_readonly(client, api_client_runtime) -> None:
    _service, repository, _audit = api_client_runtime
    system_secret = issue_client_secret()
    _seed_client(
        repository,
        client_id="aicrm-mcp",
        secret=system_secret,
        purpose="mcp",
        scopes=("read", "write"),
        capabilities=("mcp_read", "mcp_execute"),
        enabled=True,
    )
    token = _token(client, "POST", ROTATE_ROUTE)
    response = client.post(
        "/api/admin/config/api-clients/aicrm-mcp/rotate-secret",
        headers=_headers(token),
        json={"confirm": True},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "system_managed_client_readonly"
    assert repository.api_clients["aicrm-mcp"].enabled is True


def test_mcp_template_is_fixed_and_audit_failure_rolls_back_create(client, api_client_runtime) -> None:
    _service, repository, _audit = api_client_runtime
    created = _create(client, client_id="operator-mcp", client_type="mcp")
    assert created.status_code == 201
    record = repository.api_clients["operator-mcp"]
    assert record.purpose == "mcp"
    assert record.audiences == ("external_integration",)
    assert record.scopes == ("read", "write")
    assert record.capabilities == ("mcp_execute", "mcp_read")

    repository.fail_api_client_audit = True
    token = _token(client, "POST", CREATE_ROUTE)
    with pytest.raises(RuntimeError, match="simulated_api_client_audit_failure"):
        client.post(
            CREATE_ROUTE,
            headers=_headers(token),
            json={
                "display_name": "事务回滚客户端",
                "client_id": "rollback-client",
                "client_type": "external_api",
                "token_ttl_minutes": 30,
                "allowed_cidrs": [],
                "confirm": True,
            },
        )
    assert "rollback-client" not in repository.api_clients
