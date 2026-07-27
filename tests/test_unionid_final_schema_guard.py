from __future__ import annotations

import pytest
from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory


LEGACY_IDENTITY_COLUMN_NAMES = (
    "external_userid",
    "external_contact_id",
    "openid",
    "payer_openid",
    "mobile_snapshot",
    "buyer_id",
    "identity_snapshot",
    "userid_snapshot",
    "respondent_key",
    "person_id",
    "target_external_userids",
)

ALLOWED_FINAL_LEGACY_IDENTITY_COLUMNS = {
    "automation_channel_entry_runtime.external_userid",
    "crm_user_identity_conflicts.external_userid",
    "crm_user_identity_conflicts.openid",
    "crm_user_identity_resolution_queue.external_userid",
    "crm_user_identity_resolution_queue.openid",
    "external_contact_bindings.external_userid",
    "external_contact_bindings.person_id",
    "radar_click_events.external_userid",
    "radar_click_events.openid",
    "radar_click_events.person_id",
    "wecom_external_contact_event_logs.external_userid",
    "wecom_external_contact_follow_users.external_userid",
    "wecom_external_contact_identity_map.external_userid",
    "wecom_external_contact_identity_map.openid",
}

BOUNDARY_PREFIXES = (
    "automation_channel_entry_runtime.",
    "crm_user_identity_",
    "external_contact_bindings.",
    "radar_click_events.",
    "wecom_external_contact_",
)


@pytest.mark.usefixtures("next_pg_schema")
def test_final_schema_legacy_identity_columns_are_only_approved_boundaries() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        rows = (
            session.execute(
                text(
                    """
                SELECT table_name || '.' || column_name AS column_ref
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND column_name = ANY(:column_names)
                ORDER BY table_name, column_name
                """
                ),
                {"column_names": list(LEGACY_IDENTITY_COLUMN_NAMES)},
            )
            .scalars()
            .all()
        )

    actual = set(rows)
    assert actual == ALLOWED_FINAL_LEGACY_IDENTITY_COLUMNS

    non_boundary = {column_ref for column_ref in actual if not column_ref.startswith(BOUNDARY_PREFIXES)}
    assert non_boundary == set()
