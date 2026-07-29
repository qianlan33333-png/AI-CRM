from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from aicrm_next.platform.admin_auth.capabilities import capabilities_for_roles
from aicrm_next.platform.admin_auth.service import CSRF_COOKIE, SESSION_COOKIE
from aicrm_next.platform.platform_foundation.auth_platform.context import AuthContext, PrincipalType
from aicrm_next.platform.platform_foundation.auth_platform.credentials import (
    CredentialHasher,
    hash_client_secret,
    issue_client_secret,
)
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord, AuthSessionRecord, SessionSubject
from aicrm_next.platform.platform_foundation.auth_platform.service import ApiClientService, AuthServiceConfig
from aicrm_next.platform.platform_foundation.auth_platform.sessions import AuthSessionService, IssuedSession
from aicrm_next.platform.admin_auth.action_token import issue_action_token
from aicrm_next.platform.shared.route_ownership import load_route_manifest


TEST_PEPPER = "pytest-admin-session-pepper-material-32-bytes"
TEST_JWT_KEY = "pytest-jwt-signing-key-material-at-least-32-bytes"
TEST_ISSUER = "https://testserver/oauth"


class InMemoryAuthSessionRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, AuthSessionRecord] = {}
        self.session_versions: dict[str, int] = {}
        self.api_clients: dict[str, ApiClientRecord] = {}

    def insert_auth_session(self, session: AuthSessionRecord) -> None:
        self.sessions[session.session_secret_hash] = session
        self.session_versions.setdefault(session.admin_user_id, session.session_version)

    def auth_session_by_hash(self, session_hash: str) -> AuthSessionRecord | None:
        session = self.sessions.get(session_hash)
        if session is None or self.session_versions.get(session.admin_user_id) != session.session_version:
            return None
        return session

    def revoke_auth_session(self, session_hash: str, *, revoked_at: datetime, reason: str) -> bool:
        session = self.sessions.get(session_hash)
        if session is None:
            return False
        self.sessions[session_hash] = replace(session, revoked_at=revoked_at, revoked_reason=reason)
        return True

    def api_client(self, client_id: str) -> ApiClientRecord | None:
        return self.api_clients.get(client_id)

    def insert_api_client(self, client: ApiClientRecord) -> None:
        if client.client_id in self.api_clients:
            raise ValueError("duplicate client")
        self.api_clients[client.client_id] = client

    def update_api_client_definition(self, client: ApiClientRecord) -> bool:
        current = self.api_clients.get(client.client_id)
        if current is None:
            return False
        self.api_clients[client.client_id] = replace(client, secret_hash=current.secret_hash, auth_version=current.auth_version)
        return True

    def rotate_api_client_secret(self, client_id: str, secret_hash: str) -> int | None:
        current = self.api_clients.get(client_id)
        if current is None:
            return None
        updated = replace(current, secret_hash=secret_hash, auth_version=current.auth_version + 1)
        self.api_clients[client_id] = updated
        return updated.auth_version

    def set_api_client_enabled(self, client_id: str, enabled: bool) -> int | None:
        current = self.api_clients.get(client_id)
        if current is None:
            return None
        updated = replace(current, enabled=enabled, auth_version=current.auth_version + 1)
        self.api_clients[client_id] = updated
        return updated.auth_version


def install_admin_auth_service(client: TestClient) -> tuple[AuthSessionService, InMemoryAuthSessionRepository]:
    configured = getattr(client.app.state, "auth_session_service", None)
    if isinstance(configured, AuthSessionService) and isinstance(configured.repository, InMemoryAuthSessionRepository):
        return configured, configured.repository
    repository = InMemoryAuthSessionRepository()
    service = AuthSessionService(repository, CredentialHasher(TEST_PEPPER))
    client.app.state.auth_session_service = service
    client.app.state.auth_client_service = ApiClientService(
        repository,
        AuthServiceConfig(issuer=TEST_ISSUER, signing_key=TEST_JWT_KEY),
    )
    return service, repository


def install_admin_session(
    client: TestClient,
    *roles: str,
    subject: str = "admin:test",
    principal_id: str = "admin-user:test",
    session_version: int = 1,
    set_csrf_header: bool = True,
) -> IssuedSession:
    actual_roles = roles or ("super_admin",)
    capabilities = tuple(sorted(capabilities_for_roles(actual_roles)))
    scopes = ("admin.read", "admin.write") if set(capabilities) - {"admin_read", "read_customer"} else ("admin.read",)
    service, repository = install_admin_auth_service(client)
    admin_user_id = principal_id.removeprefix("admin-user:") or "test"
    repository.session_versions[admin_user_id] = int(session_version)
    issued = service.issue(
        subject=SessionSubject(
            principal_id=principal_id,
            admin_user_id=admin_user_id,
            corp_id="corp-pytest",
        ),
        session_version=session_version,
        scopes=scopes,
        capabilities=capabilities,
        now=datetime.now(timezone.utc),
    )
    client.cookies.set(SESSION_COOKIE, issued.session_cookie)
    client.cookies.set(CSRF_COOKIE, issued.csrf_token)
    if set_csrf_header:
        client.headers["X-CSRF-Token"] = issued.csrf_token
    return issued


def admin_session_cookies(client: TestClient, *roles: str) -> dict[str, str]:
    issued = install_admin_session(client, *(roles or ("super_admin",)))
    return {SESSION_COOKIE: issued.session_cookie, CSRF_COOKIE: issued.csrf_token}


def install_admin_action_tokens(
    client: TestClient,
    *routes: tuple[str, str],
    roles: tuple[str, ...] = ("super_admin",),
) -> dict[tuple[str, str], str]:
    issued = install_admin_session(client, *roles)
    service = client.app.state.auth_session_service
    introspection = service.introspect(issued.session_cookie)
    assert introspection.active and introspection.context is not None
    entries = load_route_manifest("docs/architecture/route_ownership_manifest.yml")
    tokens: dict[tuple[str, str], str] = {}
    for method, target in routes:
        normalized_method = str(method).upper()
        entry = next(
            item
            for item in entries
            if item["path"] == target and normalized_method in set(item.get("methods") or ())
        )
        tokens[(normalized_method, target)] = issue_action_token(
            introspection.context,
            capability=str(entry["capability"]),
            method=normalized_method,
            action=str(entry["route_name"]),
            target=target,
            session_binding=issued.session_id,
        )
    return tokens


def _purpose(client_id: str, capabilities: tuple[str, ...]) -> str:
    if any(capability.startswith("mcp_") for capability in capabilities):
        return "mcp"
    if "identity_resolve" in capabilities:
        return "identity"
    if any(capability.startswith("archive_") for capability in capabilities):
        return "archive"
    if "group_broadcast_execute" in capabilities:
        return "group_broadcast"
    if any(capability.startswith("campaign_") for capability in capabilities):
        return "campaign_agent"
    if "external_read" in capabilities or "external_write" in capabilities:
        return "external_agent"
    return "automation_worker"


def install_access_token(
    client: TestClient,
    *,
    audience: str,
    capabilities: tuple[str, ...],
    scopes: tuple[str, ...] = ("read",),
    client_id: str = "pytest-service-client",
    subject: str = "service:pytest",
    sender_constraint: str = "",
    resource_constraints: dict[str, Any] | None = None,
    purpose: str = "",
) -> str:
    del sender_constraint
    _session_service, repository = install_admin_auth_service(client)
    secret = issue_client_secret()
    repository.api_clients[client_id] = ApiClientRecord(
        client_id=client_id,
        principal_id=subject,
        principal_type=PrincipalType.SERVICE,
        purpose=purpose or _purpose(client_id, capabilities),
        display_name="Pytest service",
        secret_hash=hash_client_secret(secret),
        audiences=(audience,),
        scopes=tuple(sorted(set(scopes))),
        capabilities=tuple(sorted(set(capabilities))),
        allowed_cidrs=(),
        corp_id="corp-pytest",
        owner_scope=dict(resource_constraints or {}),
        auth_version=1,
        token_ttl_seconds=1800,
        enabled=True,
    )
    service = ApiClientService(repository, AuthServiceConfig(issuer=TEST_ISSUER, signing_key=TEST_JWT_KEY))
    client.app.state.auth_client_service = service
    token = service.issue_client_credentials_token(
        client_id=client_id,
        client_secret=secret,
        audience=audience,
        requested_scopes=scopes,
    )
    return token.access_token


def access_token_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_context(
    *roles: str,
    subject: str = "admin:test",
    token_id: str = "session-test",
    now: datetime | None = None,
) -> AuthContext:
    del now
    capabilities = tuple(sorted(capabilities_for_roles(roles or ("super_admin",))))
    return AuthContext(
        principal_type=PrincipalType.HUMAN,
        principal_id=subject,
        admin_user_id=subject.removeprefix("admin:"),
        corp_id="corp-pytest",
        scopes=("admin.read", "admin.write"),
        capabilities=capabilities,
        owner_scope={},
        auth_version=1,
        request_id=token_id,
    )
