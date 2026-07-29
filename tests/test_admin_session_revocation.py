from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from aicrm_next.platform.platform_foundation.auth_platform.credentials import CredentialHasher
from aicrm_next.platform.platform_foundation.auth_platform.models import SessionSubject
from aicrm_next.platform.platform_foundation.auth_platform.sessions import AuthSessionService
from tests.admin_auth_test_helpers import InMemoryAuthSessionRepository


ROOT = Path(__file__).resolve().parents[1]


def test_server_side_admin_version_revokes_existing_session() -> None:
    repository = InMemoryAuthSessionRepository()
    repository.session_versions["17"] = 4
    service = AuthSessionService(repository, CredentialHasher("admin-revocation-pepper-material-32-bytes"))
    now = datetime.now(timezone.utc)
    issued = service.issue(
        subject=SessionSubject(
            principal_id="admin-user:17",
            admin_user_id="17",
            corp_id="corp-test",
        ),
        session_version=4,
        scopes=("admin.read",),
        capabilities=("admin_read",),
        now=now,
    )
    assert service.introspect(issued.session_cookie, now=now + timedelta(minutes=1)).active

    repository.session_versions["17"] = 5
    result = service.introspect(issued.session_cookie, now=now + timedelta(minutes=2))
    assert not result.active
    assert result.error == "session_expired_or_revoked"


def test_auth_migration_joins_sessions_to_live_admin_version() -> None:
    source = (ROOT / "aicrm_next/platform/platform_foundation/auth_platform/repository.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/0104_auth_platform.py").read_text(encoding="utf-8")

    assert "JOIN admin_users a ON a.id = s.admin_user_id" in source
    assert "a.session_version = s.session_version" in source
    assert "admin_user_id BIGINT NOT NULL REFERENCES admin_users(id)" in migration
