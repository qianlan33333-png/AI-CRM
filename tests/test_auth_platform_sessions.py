from dataclasses import replace
from datetime import datetime, timedelta, timezone

from aicrm_next.platform.platform_foundation.auth_platform.credentials import CredentialHasher
from aicrm_next.platform.platform_foundation.auth_platform.models import SessionSubject
from aicrm_next.platform.platform_foundation.auth_platform.sessions import AuthSessionService


NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)


class _Repository:
    def __init__(self):
        self.sessions = {}

    def insert_auth_session(self, session):
        self.sessions[session.session_secret_hash] = session

    def auth_session_by_hash(self, session_hash):
        return self.sessions.get(session_hash)

    def revoke_auth_session(self, session_hash, *, revoked_at, reason):
        session = self.sessions.get(session_hash)
        if session is None:
            return False
        self.sessions[session_hash] = replace(session, revoked_at=revoked_at, revoked_reason=reason)
        return True


def _issue(service: AuthSessionService, admin_user_id: str = "1"):
    return service.issue(
        subject=SessionSubject(
            principal_id=f"admin-user:{admin_user_id}",
            admin_user_id=admin_user_id,
            corp_id="corp-test",
        ),
        session_version=3,
        scopes=("admin.read",),
        capabilities=("admin_read",),
        now=NOW,
    )


def test_server_session_yields_human_context_and_hash_only_storage() -> None:
    repository = _Repository()
    service = AuthSessionService(repository, CredentialHasher("session-test-pepper-material-32-bytes"))
    issued = _issue(service)

    stored = next(iter(repository.sessions.values()))
    assert issued.session_cookie.startswith("ss_")
    assert issued.csrf_token.startswith("csrf_")
    assert issued.session_cookie not in repr(stored)
    assert issued.csrf_token not in repr(stored)
    introspection = service.introspect(issued.session_cookie, now=NOW + timedelta(hours=1))
    assert introspection.active
    assert introspection.context is not None
    assert introspection.context.principal_id == "admin-user:1"
    assert introspection.context.admin_user_id == "1"
    assert service.verify_csrf(introspection, issued.csrf_token, issued.csrf_token)
    assert not service.verify_csrf(introspection, issued.csrf_token, "csrf_wrong")


def test_logout_revokes_session_immediately() -> None:
    repository = _Repository()
    service = AuthSessionService(repository, CredentialHasher("session-test-pepper-material-32-bytes"))
    issued = _issue(service, "2")

    assert service.revoke(issued.session_cookie, reason="logout", now=NOW + timedelta(minutes=1))
    result = service.introspect(issued.session_cookie, now=NOW + timedelta(minutes=1))
    assert not result.active
    assert result.error == "session_expired_or_revoked"
