from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from aicrm_next.platform.platform_foundation.auth_platform.credentials import hash_client_secret, issue_client_secret
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.profiles import API_CLIENT_PROFILE_BY_PURPOSE
from aicrm_next.platform.platform_foundation.auth_platform.service import ApiClientService, AuthServiceConfig


PROBE_ISSUER = "https://probe.invalid/oauth"
PROBE_SIGNING_KEY = "local-readiness-probe-signing-key-at-least-32-bytes"


class ProbeAuthRepository:
    def __init__(self) -> None:
        self.clients: dict[str, ApiClientRecord] = {}

    def api_client(self, client_id: str) -> ApiClientRecord | None:
        return self.clients.get(client_id)

    def insert_api_client(self, client: ApiClientRecord) -> None:
        if client.client_id in self.clients:
            raise ValueError("duplicate client")
        self.clients[client.client_id] = client

    def update_api_client_definition(self, client: ApiClientRecord) -> bool:
        current = self.clients.get(client.client_id)
        if current is None:
            return False
        self.clients[client.client_id] = replace(
            client,
            secret_hash=current.secret_hash,
            auth_version=current.auth_version,
        )
        return True

    def rotate_api_client_secret(
        self,
        client_id: str,
        secret_hash: str,
        credential_hint: str | None = None,
    ) -> int | None:
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

    def set_api_client_enabled(self, client_id: str, enabled: bool) -> int | None:
        current = self.clients.get(client_id)
        if current is None:
            return None
        updated = replace(current, enabled=enabled, auth_version=current.auth_version + 1)
        self.clients[client_id] = updated
        return updated.auth_version


def install_probe_access_token(
    client: TestClient,
    *,
    purpose: str,
    audience: str,
    scopes: tuple[str, ...],
) -> str:
    # Some checker unit tests replace TestClient with a transport-only fake.
    # It never executes middleware, but still needs a non-secret header value.
    if not hasattr(client, "app"):
        return "local-probe.header.signature"
    profile = API_CLIENT_PROFILE_BY_PURPOSE[str(purpose)]
    if audience not in profile.audiences or not set(scopes).issubset(profile.scopes):
        raise ValueError("probe client request exceeds registered profile")
    repository = ProbeAuthRepository()
    secret = issue_client_secret()
    repository.insert_api_client(
        ApiClientRecord(
            client_id=profile.client_id,
            principal_id=profile.principal_id,
            principal_type=profile.principal_type,
            purpose=profile.purpose,
            display_name=f"Local probe: {profile.display_name}",
            secret_hash=hash_client_secret(secret),
            audiences=profile.audiences,
            scopes=profile.scopes,
            capabilities=profile.capabilities,
            allowed_cidrs=(),
            corp_id="probe-corp",
            owner_scope={},
            auth_version=1,
            token_ttl_seconds=300,
            enabled=True,
        )
    )
    service = ApiClientService(
        repository,
        AuthServiceConfig(issuer=PROBE_ISSUER, signing_key=PROBE_SIGNING_KEY),
    )
    client.app.state.auth_client_service = service
    return service.issue_client_credentials_token(
        client_id=profile.client_id,
        client_secret=secret,
        audience=audience,
        requested_scopes=scopes,
    ).access_token


__all__ = ["ProbeAuthRepository", "install_probe_access_token"]
