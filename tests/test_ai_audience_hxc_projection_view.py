from __future__ import annotations

import importlib
from pathlib import Path
import re

import pytest
from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory


MIGRATION = importlib.import_module(
    "migrations.versions.0151_ai_audience_hxc_projection_view"
)


def test_hxc_projection_view_cutover_never_rebuilds_online_source_facts() -> None:
    source = Path(MIGRATION.__file__).read_text(encoding="utf-8")
    upgrade_source = source.split("def downgrade()", 1)[0]

    assert 'revision = "0151_ai_audience_hxc_projection_view"' in source
    assert 'down_revision = "0150_crm_identity_updated_cursor_index"' in source
    assert "CREATE OR REPLACE VIEW audience_read.huangxiaocan_member_usage_status_v1" in upgrade_source
    assert "ai_audience_hxc_member_usage_projection_control" in upgrade_source
    assert "ai_audience_hxc_member_usage_projection projection" in upgrade_source
    assert "projection.generation = (" in upgrade_source
    assert "SELECT control.active_generation" in upgrade_source
    assert "JOIN LATERAL" in upgrade_source
    assert "wecom_external_contact_identity_map identity_map" in upgrade_source
    assert "identity_map.unionid = projection.unionid" in upgrade_source
    assert "COALESCE(identity_map.follow_user_userid, '') =" in upgrade_source
    assert "audience_read.wecom_contacts_v1" not in upgrade_source
    assert "SELECT *" not in upgrade_source.upper()
    assert "user_ops_hxc_dashboard_snapshot" not in upgrade_source
    assert "automation_laohuang_chat_job" not in upgrade_source
    assert "new_version_user_subscriptions" not in upgrade_source
    assert "membership_sources AS" not in upgrade_source
    assert not re.search(r"\b(?:JOIN|ON)\b[^\n]{0,240}\bOR\b", upgrade_source, flags=re.IGNORECASE)
    assert "DROP TABLE" not in source.upper()


@pytest.mark.usefixtures("next_pg_schema")
def test_hxc_projection_view_reads_only_active_generation_during_rebuild() -> None:
    session_factory = get_session_factory()
    with session_factory.begin() as session:
        session.execute(
            text(
                """
                INSERT INTO people (id, mobile, third_party_user_id, updated_at)
                VALUES (99501, '13950000001', 'wm_projection_view', CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO external_contact_bindings (
                    external_userid, person_id, first_owner_userid,
                    last_owner_userid, updated_at
                ) VALUES (
                    'wm_projection_view', '99501',
                    'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP
                )
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO wecom_external_contact_identity_map (
                    external_userid, unionid, follow_user_userid,
                    name, status, updated_at
                ) VALUES (
                    'wm_projection_view', 'union_projection_view',
                    'HuangYouCan', '投影视图', 'active', CURRENT_TIMESTAMP
                )
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO ai_audience_hxc_member_usage_projection (
                    generation, unionid, owner_userid, mobile_hash,
                    is_member, is_registered, has_real_usage,
                    membership_status, payload_json
                ) VALUES
                    (
                        1, 'union_projection_view', 'HuangYouCan', md5('13950000001'),
                        TRUE, TRUE, FALSE, 'active', '{"generation": 1}'::jsonb
                    ),
                    (
                        2, 'union_projection_view', 'HuangYouCan', md5('13950000001'),
                        FALSE, TRUE, TRUE, 'expired', '{"generation": 2}'::jsonb
                    )
                """
            )
        )
        session.execute(
            text(
                """
                UPDATE ai_audience_hxc_member_usage_projection_control
                SET active_generation = 1,
                    status = 'building',
                    projected_row_count = 1
                WHERE singleton = TRUE
                """
            )
        )

    with session_factory() as session:
        building_row = session.execute(
            text(
                """
                SELECT external_userid, is_member, is_registered,
                       has_real_usage, membership_status, payload_json
                FROM audience_read.huangxiaocan_member_usage_status_v1
                WHERE owner_userid = 'HuangYouCan'
                """
            )
        ).mappings().one()
        session.execute(
            text(
                """
                UPDATE ai_audience_hxc_member_usage_projection_control
                SET active_generation = 2,
                    status = 'ready',
                    projected_row_count = 1
                WHERE singleton = TRUE
                """
            )
        )
        session.commit()

    with session_factory() as session:
        switched_row = session.execute(
            text(
                """
                SELECT external_userid, is_member, is_registered,
                       has_real_usage, membership_status, payload_json
                FROM audience_read.huangxiaocan_member_usage_status_v1
                WHERE owner_userid = 'HuangYouCan'
                """
            )
        ).mappings().one()

    assert dict(building_row) == {
        "external_userid": "wm_projection_view",
        "is_member": True,
        "is_registered": True,
        "has_real_usage": False,
        "membership_status": "active",
        "payload_json": {"generation": 1},
    }
    assert dict(switched_row) == {
        "external_userid": "wm_projection_view",
        "is_member": False,
        "is_registered": True,
        "has_real_usage": True,
        "membership_status": "expired",
        "payload_json": {"generation": 2},
    }
