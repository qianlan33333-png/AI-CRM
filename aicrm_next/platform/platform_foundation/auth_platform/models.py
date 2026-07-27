from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .context import PrincipalType


@dataclass(frozen=True)
class ApiClientRecord:
    client_id: str
    principal_id: str
    principal_type: PrincipalType
    purpose: str
    display_name: str
    secret_hash: str
    audiences: tuple[str, ...]
    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    allowed_cidrs: tuple[str, ...]
    corp_id: str
    owner_scope: dict[str, Any]
    auth_version: int
    token_ttl_seconds: int
    enabled: bool


@dataclass(frozen=True)
class WebhookClientRecord:
    client_id: str
    principal_id: str
    display_name: str
    secret_reference: str
    capabilities: tuple[str, ...]
    allowed_cidrs: tuple[str, ...]
    corp_id: str
    owner_scope: dict[str, Any]
    auth_version: int
    enabled: bool


@dataclass(frozen=True)
class SessionSubject:
    principal_id: str
    admin_user_id: str
    corp_id: str


@dataclass(frozen=True)
class AuthSessionRecord:
    session_id: str
    session_secret_hash: str
    csrf_token_hash: str
    principal_id: str
    admin_user_id: str
    corp_id: str
    session_version: int
    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    owner_scope: dict[str, Any]
    auth_time: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revoked_reason: str


@dataclass(frozen=True)
class IssuedAccessToken:
    access_token: str
    token_type: str
    expires_in: int
    scope: str
