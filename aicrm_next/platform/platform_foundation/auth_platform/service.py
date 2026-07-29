from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import ipaddress
from threading import RLock
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

import jwt

from .context import AuthContext, PrincipalType
from .credentials import hash_client_secret, issue_client_secret, verify_client_secret
from .models import ApiClientRecord, IssuedAccessToken


DEFAULT_TOKEN_TTL_SECONDS = 30 * 60
MAX_TOKEN_TTL_SECONDS = 60 * 60
CLIENT_CACHE_TTL_SECONDS = 30


class ApiClientRepository(Protocol):
    def api_client(self, client_id: str) -> ApiClientRecord | None: ...

    def insert_api_client(self, client: ApiClientRecord) -> None: ...

    def update_api_client_definition(self, client: ApiClientRecord) -> bool: ...

    def rotate_api_client_secret(self, client_id: str, secret_hash: str) -> int | None: ...

    def set_api_client_enabled(self, client_id: str, enabled: bool) -> int | None: ...


class AuthError(ValueError):
    def __init__(self, error: str, *, status_code: int = 401) -> None:
        super().__init__(error)
        self.error = error
        self.status_code = status_code


@dataclass(frozen=True)
class AuthServiceConfig:
    issuer: str
    signing_key: str
    algorithm: str = "HS256"

    def __post_init__(self) -> None:
        if not str(self.issuer or "").strip():
            raise ValueError("auth issuer is required")
        if len(str(self.signing_key or "").encode("utf-8")) < 32:
            raise ValueError("JWT signing key must contain at least 32 bytes")
        if self.algorithm != "HS256":
            raise ValueError("only HS256 is supported by the private-deployment auth runtime")


@dataclass(frozen=True)
class IssuedClientSecret:
    client: ApiClientRecord
    client_secret: str


class ApiClientService:
    def __init__(self, repository: ApiClientRepository, config: AuthServiceConfig) -> None:
        self.repository = repository
        self.config = config
        self._cache: dict[str, tuple[float, ApiClientRecord]] = {}
        self._cache_lock = RLock()

    def create_client(
        self,
        *,
        client_id: str,
        principal_id: str,
        principal_type: PrincipalType,
        purpose: str,
        display_name: str,
        audiences: tuple[str, ...],
        scopes: tuple[str, ...],
        capabilities: tuple[str, ...],
        allowed_cidrs: tuple[str, ...] = (),
        corp_id: str = "",
        owner_scope: dict[str, Any] | None = None,
        token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
        enabled: bool = True,
    ) -> IssuedClientSecret:
        normalized_client_id = _required(client_id, "client_id")
        if principal_type not in {PrincipalType.API_CLIENT, PrincipalType.SERVICE}:
            raise ValueError("API client principal type must be api_client or service")
        if self.repository.api_client(normalized_client_id) is not None:
            raise ValueError("client_id already exists")
        secret = issue_client_secret()
        record = ApiClientRecord(
            client_id=normalized_client_id,
            principal_id=_required(principal_id, "principal_id"),
            principal_type=principal_type,
            purpose=_required(purpose, "purpose"),
            display_name=str(display_name or "").strip() or normalized_client_id,
            secret_hash=hash_client_secret(secret),
            audiences=_values(audiences),
            scopes=_values(scopes),
            capabilities=_values(capabilities),
            allowed_cidrs=_validated_cidrs(allowed_cidrs),
            corp_id=str(corp_id or "").strip(),
            owner_scope=dict(owner_scope or {}),
            auth_version=1,
            token_ttl_seconds=_token_ttl(token_ttl_seconds),
            enabled=bool(enabled),
        )
        if not record.audiences or not record.scopes or not record.capabilities:
            raise ValueError("API client audiences, scopes, and capabilities are required")
        self.repository.insert_api_client(record)
        self.invalidate(normalized_client_id)
        return IssuedClientSecret(client=record, client_secret=secret)

    def reconcile_client(self, desired: ApiClientRecord) -> bool:
        current = self.repository.api_client(desired.client_id)
        if current is None:
            self.repository.insert_api_client(desired)
            self.invalidate(desired.client_id)
            return True
        immutable = (current.principal_id, current.principal_type)
        if immutable != (desired.principal_id, desired.principal_type):
            raise ValueError("client principal identity is immutable")
        changed = self.repository.update_api_client_definition(
            replace(
                desired,
                secret_hash=current.secret_hash,
                auth_version=current.auth_version,
                enabled=current.enabled,
            )
        )
        if current.enabled != desired.enabled:
            self.repository.set_api_client_enabled(desired.client_id, desired.enabled)
            changed = True
        self.invalidate(desired.client_id)
        return changed

    def rotate_secret(self, client_id: str) -> IssuedClientSecret:
        current = self.repository.api_client(_required(client_id, "client_id"))
        if current is None:
            raise ValueError("client_not_found")
        secret = issue_client_secret()
        auth_version = self.repository.rotate_api_client_secret(current.client_id, hash_client_secret(secret))
        if auth_version is None:
            raise ValueError("client_not_found")
        self.invalidate(current.client_id)
        return IssuedClientSecret(client=replace(current, secret_hash="", auth_version=auth_version), client_secret=secret)

    def set_enabled(self, client_id: str, enabled: bool) -> int:
        auth_version = self.repository.set_api_client_enabled(_required(client_id, "client_id"), bool(enabled))
        if auth_version is None:
            raise ValueError("client_not_found")
        self.invalidate(client_id)
        return auth_version

    def issue_client_credentials_token(
        self,
        *,
        client_id: str,
        client_secret: str,
        audience: str,
        requested_scopes: tuple[str, ...],
        source_ip: str = "",
        now: datetime | None = None,
    ) -> IssuedAccessToken:
        client = self.repository.api_client(_required(client_id, "client_id"))
        if client is None or not client.enabled or not verify_client_secret(client_secret, client.secret_hash):
            raise AuthError("invalid_client")
        if not _ip_allowed(source_ip, client.allowed_cidrs):
            raise AuthError("client_ip_not_allowed", status_code=403)
        normalized_audience = _required(audience, "audience")
        if normalized_audience not in client.audiences:
            raise AuthError("invalid_target", status_code=403)
        scopes = _values(requested_scopes)
        if not scopes or not set(scopes).issubset(client.scopes):
            raise AuthError("invalid_scope", status_code=403)
        issued_at = _utc(now or datetime.now(timezone.utc))
        ttl = _token_ttl(client.token_ttl_seconds)
        expires_at = issued_at + timedelta(seconds=ttl)
        jti = f"jwt_{uuid4().hex}"
        claims = {
            "iss": self.config.issuer.rstrip("/"),
            "aud": normalized_audience,
            "sub": client.principal_id,
            "client_id": client.client_id,
            "scope": " ".join(scopes),
            "capabilities": list(client.capabilities),
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": jti,
            "auth_version": client.auth_version,
            "principal_type": client.principal_type.value,
            "corp_id": client.corp_id,
            "owner_scope": client.owner_scope,
        }
        access_token = jwt.encode(claims, self.config.signing_key, algorithm=self.config.algorithm)
        return IssuedAccessToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ttl,
            scope=" ".join(scopes),
        )

    def verify_access_token(
        self,
        token: str,
        *,
        audience: str,
        source_ip: str = "",
        request_id: str = "",
        client_purpose: str = "",
        now: datetime | None = None,
    ) -> AuthContext:
        current = _utc(now or datetime.now(timezone.utc))
        try:
            claims = jwt.decode(
                str(token or ""),
                self.config.signing_key,
                algorithms=[self.config.algorithm],
                issuer=self.config.issuer.rstrip("/"),
                leeway=30,
                options={
                    "verify_aud": False,
                    "require": ["iss", "aud", "sub", "client_id", "scope", "iat", "exp", "jti", "auth_version"],
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("access_token_expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid_access_token") from exc
        if datetime.fromtimestamp(int(claims["iat"]), tz=timezone.utc) > current + timedelta(seconds=30):
            raise AuthError("invalid_access_token")
        required_audience = _required(audience, "audience")
        token_audiences = claims.get("aud")
        normalized_token_audiences = (
            {str(value) for value in token_audiences}
            if isinstance(token_audiences, (list, tuple, set))
            else {str(token_audiences or "")}
        )
        if required_audience not in normalized_token_audiences:
            raise AuthError("invalid_target", status_code=403)
        client_id = str(claims.get("client_id") or "").strip()
        client = self._cached_client(client_id)
        if client is None or not client.enabled:
            raise AuthError("client_disabled")
        if required_audience not in client.audiences or not normalized_token_audiences.issubset(client.audiences):
            raise AuthError("invalid_target", status_code=403)
        if client_purpose and client.purpose != str(client_purpose).strip():
            raise AuthError("client_purpose_forbidden", status_code=403)
        if int(claims.get("auth_version") or 0) != client.auth_version:
            raise AuthError("stale_auth_version")
        if not _ip_allowed(source_ip, client.allowed_cidrs):
            raise AuthError("client_ip_not_allowed", status_code=403)
        scopes = _values(str(claims.get("scope") or "").split())
        capabilities = _values(tuple(claims.get("capabilities") or ()))
        if not set(scopes).issubset(client.scopes) or not set(capabilities).issubset(client.capabilities):
            raise AuthError("invalid_access_token")
        if str(claims.get("sub") or "") != client.principal_id:
            raise AuthError("invalid_access_token")
        return AuthContext(
            principal_type=client.principal_type,
            principal_id=client.principal_id,
            client_id=client.client_id,
            corp_id=client.corp_id,
            scopes=scopes,
            capabilities=capabilities,
            owner_scope=client.owner_scope,
            auth_version=client.auth_version,
            request_id=str(request_id or claims.get("jti") or ""),
        )

    def invalidate(self, client_id: str) -> None:
        with self._cache_lock:
            self._cache.pop(str(client_id or "").strip(), None)

    def _cached_client(self, client_id: str) -> ApiClientRecord | None:
        key = str(client_id or "").strip()
        timestamp = monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and cached[0] > timestamp:
                return cached[1]
        record = self.repository.api_client(key)
        if record is not None:
            with self._cache_lock:
                self._cache[key] = (timestamp + CLIENT_CACHE_TTL_SECONDS, record)
        return record


def _required(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _values(values: tuple[str, ...] | list[str] | set[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value or "").strip() for value in values if str(value or "").strip()}))


def _validated_cidrs(values: tuple[str, ...]) -> tuple[str, ...]:
    result = []
    for value in _values(values):
        result.append(str(ipaddress.ip_network(value, strict=False)))
    return tuple(result)


def _ip_allowed(source_ip: str, allowed_cidrs: tuple[str, ...]) -> bool:
    if not allowed_cidrs:
        return True
    try:
        address = ipaddress.ip_address(str(source_ip or "").strip())
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(cidr, strict=False) for cidr in allowed_cidrs)


def _token_ttl(value: int) -> int:
    ttl = int(value or DEFAULT_TOKEN_TTL_SECONDS)
    if ttl < 60 or ttl > MAX_TOKEN_TTL_SECONDS:
        raise ValueError("token TTL must be between 60 and 3600 seconds")
    return ttl


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("auth timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
