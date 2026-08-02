from dataclasses import replace

import jwt
import pytest

from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from aicrm_next.platform.platform_foundation.auth_platform.credentials import hash_client_secret, issue_client_secret
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.service import ApiClientService, AuthError, AuthServiceConfig


SIGNING_KEY = "unit-test-jwt-signing-key-material-at-least-32-bytes"
ISSUER = "https://crm.example.test/oauth"


class _Repository:
    def __init__(self) -> None:
        self.clients: dict[str, ApiClientRecord] = {}

    def api_client(self, client_id: str):
        return self.clients.get(client_id)

    def insert_api_client(self, client: ApiClientRecord) -> None:
        if client.client_id in self.clients:
            raise ValueError("duplicate")
        self.clients[client.client_id] = client

    def update_api_client_definition(self, client: ApiClientRecord) -> bool:
        current = self.clients.get(client.client_id)
        if current is None:
            return False
        self.clients[client.client_id] = replace(client, secret_hash=current.secret_hash, auth_version=current.auth_version)
        return True

    def rotate_api_client_secret(self, client_id: str, secret_hash: str, credential_hint: str | None = None):
        current = self.clients.get(client_id)
        if current is None:
            return None
        updated = replace(
            current,
            secret_hash=secret_hash,
            auth_version=current.auth_version + 1,
            credential_hint=credential_hint or current.credential_hint,
        )
        self.clients[client_id] = updated
        return updated.auth_version

    def set_api_client_enabled(self, client_id: str, enabled: bool):
        current = self.clients.get(client_id)
        if current is None:
            return None
        updated = replace(current, enabled=enabled, auth_version=current.auth_version + 1)
        self.clients[client_id] = updated
        return updated.auth_version


def _service() -> tuple[ApiClientService, _Repository, str]:
    repository = _Repository()
    secret = issue_client_secret()
    repository.clients["campaign-agent"] = ApiClientRecord(
        client_id="campaign-agent",
        principal_id="api_client:campaign_agent",
        principal_type=PrincipalType.API_CLIENT,
        purpose="campaign_agent",
        display_name="Campaign Agent",
        secret_hash=hash_client_secret(secret),
        audiences=("external_integration",),
        scopes=("read", "write"),
        capabilities=("campaign_draft_create", "campaign_status_read"),
        allowed_cidrs=("203.0.113.0/24",),
        corp_id="corp-1",
        owner_scope={"owner_userid": ["owner-1"]},
        auth_version=1,
        token_ttl_seconds=1800,
        enabled=True,
    )
    return ApiClientService(repository, AuthServiceConfig(issuer=ISSUER, signing_key=SIGNING_KEY)), repository, secret


def test_client_credentials_issues_required_short_lived_signed_claims() -> None:
    service, _repository, secret = _service()
    issued = service.issue_client_credentials_token(
        client_id="campaign-agent",
        client_secret=secret,
        audience="external_integration",
        requested_scopes=("write",),
        source_ip="203.0.113.8",
    )
    claims = jwt.decode(issued.access_token, SIGNING_KEY, algorithms=["HS256"], options={"verify_aud": False})

    assert issued.token_type == "Bearer"
    assert issued.expires_in == 1800
    assert {"iss", "aud", "sub", "client_id", "scope", "iat", "exp", "jti", "auth_version"}.issubset(claims)
    assert claims["client_id"] == "campaign-agent"
    assert claims["auth_version"] == 1
    assert claims["exp"] - claims["iat"] == 1800


def test_local_verification_enforces_audience_purpose_scope_capability_and_owner_scope() -> None:
    service, _repository, secret = _service()
    token = service.issue_client_credentials_token(
        client_id="campaign-agent",
        client_secret=secret,
        audience="external_integration",
        requested_scopes=("read", "write"),
        source_ip="203.0.113.9",
    ).access_token
    context = service.verify_access_token(
        token,
        audience="external_integration",
        source_ip="203.0.113.9",
        client_purpose="campaign_agent",
    )

    assert context.client_id == "campaign-agent"
    assert context.permits(
        capability="campaign_draft_create",
        scope="write",
        resource={"owner_userid": "owner-1"},
    )
    assert not context.permits(
        capability="campaign_draft_create",
        scope="write",
        resource={"owner_userid": "owner-2"},
    )
    with pytest.raises(AuthError, match="invalid_target") as wrong_audience:
        service.verify_access_token(token, audience="internal_worker", source_ip="203.0.113.9")
    assert wrong_audience.value.status_code == 403
    with pytest.raises(AuthError, match="client_purpose_forbidden"):
        service.verify_access_token(
            token,
            audience="external_integration",
            source_ip="203.0.113.9",
            client_purpose="identity",
        )


def test_wrong_secret_scope_target_or_cidr_is_rejected() -> None:
    service, _repository, secret = _service()
    with pytest.raises(AuthError, match="invalid_client"):
        service.issue_client_credentials_token(
            client_id="campaign-agent",
            client_secret=issue_client_secret(),
            audience="external_integration",
            requested_scopes=("read",),
            source_ip="203.0.113.8",
        )
    with pytest.raises(AuthError, match="invalid_scope"):
        service.issue_client_credentials_token(
            client_id="campaign-agent",
            client_secret=secret,
            audience="external_integration",
            requested_scopes=("admin.write",),
            source_ip="203.0.113.8",
        )
    with pytest.raises(AuthError, match="client_ip_not_allowed"):
        service.issue_client_credentials_token(
            client_id="campaign-agent",
            client_secret=secret,
            audience="external_integration",
            requested_scopes=("read",),
            source_ip="198.51.100.1",
        )


def test_rotation_and_disable_invalidate_already_issued_jwt_by_auth_version() -> None:
    service, _repository, secret = _service()
    token = service.issue_client_credentials_token(
        client_id="campaign-agent",
        client_secret=secret,
        audience="external_integration",
        requested_scopes=("read",),
        source_ip="203.0.113.8",
    ).access_token

    rotated = service.rotate_secret("campaign-agent")
    assert rotated.client.auth_version == 2
    with pytest.raises(AuthError, match="stale_auth_version"):
        service.verify_access_token(token, audience="external_integration", source_ip="203.0.113.8")

    replacement = service.issue_client_credentials_token(
        client_id="campaign-agent",
        client_secret=rotated.client_secret,
        audience="external_integration",
        requested_scopes=("read",),
        source_ip="203.0.113.8",
    ).access_token
    assert service.set_enabled("campaign-agent", False) == 3
    with pytest.raises(AuthError, match="client_disabled"):
        service.verify_access_token(replacement, audience="external_integration", source_ip="203.0.113.8")
