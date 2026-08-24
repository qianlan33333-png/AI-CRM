from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.channels.channel_entry import profile_description_backfill as backfill
from aicrm_next.platform.shared.db_session import get_engine, get_session_factory
from scripts.ops.backfill_wecom_profile_descriptions import _remaining_breakdown


def test_remaining_breakdown_query_is_valid_postgresql(migrated_database_url: str) -> None:
    with Session(get_engine(migrated_database_url)) as session:
        assert _remaining_breakdown(session) == {}


def test_live_description_projection_binds_text_unambiguously_in_postgresql(
    migrated_database_url: str,
    monkeypatch,
) -> None:
    external_userid = "wm_postgres_projection_001"
    owner_userid = "owner_postgres_projection_001"
    factory = get_session_factory(migrated_database_url)
    monkeypatch.setattr(backfill, "get_session_factory", lambda: factory)
    with factory() as session:
        session.execute(
            text(
                """
                INSERT INTO wecom_external_contact_follow_users (
                    corp_id, external_userid, user_id, relation_status, description, raw_follow_user
                ) VALUES (
                    'corp-postgres-test', :external_userid, :owner_userid, 'active', '', '{}'::jsonb
                )
                ON CONFLICT (corp_id, external_userid, user_id) DO UPDATE
                SET relation_status = 'active', description = '', raw_follow_user = '{}'::jsonb
                """
            ),
            {"external_userid": external_userid, "owner_userid": owner_userid},
        )
        session.commit()
    try:
        assert (
            backfill._sync_live_nonempty_descriptions(
                external_userid=external_userid,
                descriptions_by_owner={owner_userid: "wm_postgres_projection_001"},
            )
            == 1
        )
        with factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT description, raw_follow_user->>'description' AS raw_description
                    FROM wecom_external_contact_follow_users
                    WHERE corp_id = 'corp-postgres-test'
                      AND external_userid = :external_userid
                      AND user_id = :owner_userid
                    """
                ),
                {"external_userid": external_userid, "owner_userid": owner_userid},
            ).mappings().one()
        assert row["description"] == external_userid
        assert row["raw_description"] == external_userid
    finally:
        with factory() as session:
            session.execute(
                text(
                    """
                    DELETE FROM wecom_external_contact_follow_users
                    WHERE corp_id = 'corp-postgres-test'
                      AND external_userid = :external_userid
                      AND user_id = :owner_userid
                    """
                ),
                {"external_userid": external_userid, "owner_userid": owner_userid},
            )
            session.commit()
