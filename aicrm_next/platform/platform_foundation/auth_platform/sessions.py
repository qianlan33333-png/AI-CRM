from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

from .context import AuthContext, PrincipalType
from .credentials import CSRF_PREFIX, SESSION_PREFIX, CredentialHasher
from .models import AuthSessionRecord, SessionSubject


SESSION_TTL = timedelta(hours=8)


class SessionRepository(Protocol):
    def insert_auth_session(self, session: AuthSessionRecord) -> None: ...

    def auth_session_by_hash(self, session_hash: str) -> AuthSessionRecord | None: ...

    def revoke_auth_session(self, session_hash: str, *, revoked_at: datetime, reason: str) -> bool: ...


@dataclass(frozen=True)
class IssuedSession:
    session_cookie: str
    csrf_token: str
    session_id: str
    expires_at: datetime


@dataclass(frozen=True)
class SessionIntrospection:
    active: bool
    context: AuthContext | None = None
    record: AuthSessionRecord | None = None
    error: str = ""


class AuthSessionService:
    def __init__(self, repository: SessionRepository, hasher: CredentialHasher) -> None:
        self._repository = repository
        self._hasher = hasher

    @property
    def repository(self) -> SessionRepository:
        return self._repository

    def issue(
        self,
        *,
        subject: SessionSubject,
        session_version: int,
        scopes: tuple[str, ...],
        capabilities: tuple[str, ...],
        owner_scope: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> IssuedSession:
        issued_at = _utc(now or datetime.now(timezone.utc))
        if int(session_version or 0) < 1:
            raise ValueError("session version must be positive")
        if not str(subject.admin_user_id or "").strip():
            raise ValueError("human session requires admin_user_id")
        session_credential = self._hasher.issue(SESSION_PREFIX)
        csrf_credential = self._hasher.issue(CSRF_PREFIX)
        session_id = f"session_{uuid4().hex}"
        expires_at = issued_at + SESSION_TTL
        self._repository.insert_auth_session(
            AuthSessionRecord(
                session_id=session_id,
                session_secret_hash=session_credential.digest,
                csrf_token_hash=csrf_credential.digest,
                principal_id=subject.principal_id,
                admin_user_id=subject.admin_user_id,
                corp_id=subject.corp_id,
                session_version=int(session_version),
                scopes=tuple(sorted(set(scopes))),
                capabilities=tuple(sorted(set(capabilities))),
                owner_scope=dict(owner_scope or {}),
                auth_time=issued_at,
                expires_at=expires_at,
                revoked_at=None,
                revoked_reason="",
            )
        )
        return IssuedSession(
            session_cookie=session_credential.value,
            csrf_token=csrf_credential.value,
            session_id=session_id,
            expires_at=expires_at,
        )

    def introspect(self, session_cookie: str, *, now: datetime | None = None) -> SessionIntrospection:
        try:
            digest = self._hasher.digest(session_cookie)
        except ValueError:
            return SessionIntrospection(active=False, error="session_required")
        record = self._repository.auth_session_by_hash(digest)
        current = _utc(now or datetime.now(timezone.utc))
        if record is None or record.revoked_at is not None or current >= _utc(record.expires_at):
            return SessionIntrospection(active=False, error="session_expired_or_revoked")
        return SessionIntrospection(
            active=True,
            record=record,
            context=AuthContext(
                principal_type=PrincipalType.HUMAN,
                principal_id=record.principal_id,
                admin_user_id=record.admin_user_id,
                corp_id=record.corp_id,
                scopes=record.scopes,
                capabilities=record.capabilities,
                owner_scope=record.owner_scope,
                auth_version=record.session_version,
                request_id=record.session_id,
            ),
        )

    def verify_csrf(self, introspection: SessionIntrospection, cookie_token: str, request_token: str) -> bool:
        record = introspection.record
        if not introspection.active or record is None or not cookie_token or not request_token:
            return False
        return self._hasher.verify(cookie_token, record.csrf_token_hash) and self._hasher.verify(
            request_token,
            record.csrf_token_hash,
        )

    def revoke(self, session_cookie: str, *, reason: str, now: datetime | None = None) -> bool:
        try:
            digest = self._hasher.digest(session_cookie)
        except ValueError:
            return False
        return self._repository.revoke_auth_session(
            digest,
            revoked_at=_utc(now or datetime.now(timezone.utc)),
            reason=str(reason or "logout")[:128],
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("session timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
