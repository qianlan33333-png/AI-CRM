from datetime import datetime, timedelta, timezone
import os

from sqlalchemy import text

from aicrm_next.platform.platform_foundation.auth_platform.credentials import CredentialHasher
from aicrm_next.platform.platform_foundation.auth_platform.models import SessionSubject
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository
from aicrm_next.platform.platform_foundation.auth_platform.sessions import AuthSessionService
from aicrm_next.platform.shared.db_session import get_session_factory


def test_postgres_session_is_invalidated_by_admin_session_version(next_pg_schema) -> None:
    repository = PostgresAuthRepository(database_url=os.environ["DATABASE_URL"])
    with get_session_factory().begin() as session:
        admin_user_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO admin_users (
                        wecom_userid, wecom_corpid, display_name, is_active,
                        login_enabled, session_version
                    ) VALUES (
                        'pytest-auth-session', 'corp-test', 'Session admin', TRUE, TRUE, 4
                    ) RETURNING id
                    """
                )
            ).scalar_one()
        )
    service = AuthSessionService(repository, CredentialHasher("postgres-session-pepper-32-bytes"))
    now = datetime.now(timezone.utc)
    issued = service.issue(
        subject=SessionSubject(
            principal_id=f"admin-user:{admin_user_id}",
            admin_user_id=str(admin_user_id),
            corp_id="corp-test",
        ),
        session_version=4,
        scopes=("admin.read",),
        capabilities=("admin_read",),
        now=now,
    )
    assert service.introspect(issued.session_cookie, now=now + timedelta(minutes=1)).active

    with get_session_factory().begin() as session:
        session.execute(
            text("UPDATE admin_users SET session_version = session_version + 1 WHERE id = :id"),
            {"id": admin_user_id},
        )
    result = service.introspect(issued.session_cookie, now=now + timedelta(minutes=2))
    assert not result.active
    assert result.error == "session_expired_or_revoked"
