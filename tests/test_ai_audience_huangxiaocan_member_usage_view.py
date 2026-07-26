from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from sqlalchemy import text

from aicrm_next.ai_audience_ops.hxc_projection import HxcMemberUsageProjectionService
from aicrm_next.ai_audience_ops.simple_sql import validate_simple_sql
from aicrm_next.ai_audience_ops.sql_catalog import ALLOWED_VIEWS, schema_catalog_payload
from aicrm_next.shared.db_session import get_session_factory


MIGRATION = importlib.import_module("migrations.versions.0151_ai_audience_hxc_projection_view")


def test_huangxiaocan_member_usage_view_is_simple_sql_catalog_allowed() -> None:
    sql = """
        SELECT DISTINCT external_userid
        FROM audience_read.huangxiaocan_member_usage_status_v1
        WHERE owner_userid = :owner_userid
          AND is_member = true
          AND is_registered = true
          AND has_real_usage = false
    """

    result = validate_simple_sql(sql, {"owner_userid": "HuangYouCan"})

    assert result.ok is True
    assert result.errors == []
    assert result.dependencies == ["audience_read.huangxiaocan_member_usage_status_v1"]
    assert "audience_read.huangxiaocan_member_usage_status_v1" in ALLOWED_VIEWS
    assert any(item["name"] == "audience_read.huangxiaocan_member_usage_status_v1" for item in schema_catalog_payload()["views"])


def test_huangxiaocan_member_usage_migration_does_not_seed_business_package() -> None:
    source = Path(MIGRATION.__file__).read_text(encoding="utf-8")

    assert "INSERT INTO ai_audience_package" not in source
    assert "ai_audience_package_version" not in source
    assert "HuangYouCan" not in source


@pytest.mark.usefixtures("next_pg_schema")
def test_huangxiaocan_member_usage_view_marks_registered_members_without_usage() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO people (id, mobile, third_party_user_id, updated_at)
                VALUES
                    (99601, '13900000001', 'wm_hxc_member_unused', CURRENT_TIMESTAMP),
                    (99602, '13900000002', 'wm_hxc_member_used', CURRENT_TIMESTAMP),
                    (99603, '13900000003', 'wm_hxc_not_member', CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO external_contact_bindings (
                    external_userid, person_id, first_owner_userid, last_owner_userid, updated_at
                )
                VALUES
                    ('wm_hxc_member_unused', '99601', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP),
                    ('wm_hxc_member_used', '99602', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP),
                    ('wm_hxc_not_member', '99603', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO wecom_external_contact_identity_map (
                    external_userid, unionid, follow_user_userid, name, status, updated_at
                )
                VALUES
                    ('wm_hxc_member_unused', 'union_hxc_member_unused', 'HuangYouCan', '会员未使用', 'active', CURRENT_TIMESTAMP),
                    ('wm_hxc_member_used', 'union_hxc_member_used', 'HuangYouCan', '会员已使用', 'active', CURRENT_TIMESTAMP),
                    ('wm_hxc_not_member', 'union_hxc_not_member', 'HuangYouCan', '非会员', 'active', CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO user_ops_hxc_dashboard_snapshot (
                    mobile, phone_match_key, unionid, owner_userid,
                    hxc_member_hit, hxc_user_hit, funnel_state, hxc_registered_at,
                    membership_type, membership_status, membership_end_at,
                    conv_chat, msg_user, msg_ai, last_msg_at, refreshed_at
                )
                VALUES
                    (
                        '13900000001', '13900000001', 'union_hxc_member_unused', 'HuangYouCan',
                        true, true, 'member_no_usage', CURRENT_TIMESTAMP - interval '3 days',
                        'premium', 'active', CURRENT_TIMESTAMP + interval '30 days',
                        0, 0, 0, NULL, CURRENT_TIMESTAMP
                    ),
                    (
                        '13900000002', '13900000002', 'union_hxc_member_used', 'HuangYouCan',
                        true, true, 'member_used', CURRENT_TIMESTAMP - interval '3 days',
                        'premium', 'active', CURRENT_TIMESTAMP + interval '30 days',
                        1, 1, 2, CURRENT_TIMESTAMP - interval '1 day', CURRENT_TIMESTAMP
                    ),
                    (
                        '13900000003', '13900000003', 'union_hxc_not_member', 'HuangYouCan',
                        false, true, 'registered_no_member', CURRENT_TIMESTAMP - interval '3 days',
                        '', '', NULL,
                        0, 0, 0, NULL, CURRENT_TIMESTAMP
                    )
                """
            )
        )
        session.commit()

    refreshed = HxcMemberUsageProjectionService().refresh_full()

    assert refreshed["ok"] is True
    assert refreshed["projected_row_count"] == 3
    with session_factory() as session:
        rows = session.execute(
            text(
                """
                SELECT external_userid, is_member, is_registered, has_real_usage,
                       membership_source, registration_source, usage_source
                FROM audience_read.huangxiaocan_member_usage_status_v1
                WHERE owner_userid = 'HuangYouCan'
                ORDER BY external_userid
                """
            )
        ).mappings().all()
        target_rows = session.execute(
            text(
                """
                SELECT external_userid
                FROM audience_read.huangxiaocan_member_usage_status_v1
                WHERE owner_userid = 'HuangYouCan'
                  AND is_member = true
                  AND is_registered = true
                  AND has_real_usage = false
                ORDER BY external_userid
                """
            )
        ).mappings().all()

    by_external_userid = {row["external_userid"]: row for row in rows}
    assert by_external_userid["wm_hxc_member_unused"]["is_member"] is True
    assert by_external_userid["wm_hxc_member_unused"]["is_registered"] is True
    assert by_external_userid["wm_hxc_member_unused"]["has_real_usage"] is False
    assert "user_ops_hxc_dashboard_snapshot" in by_external_userid["wm_hxc_member_unused"]["membership_source"]
    assert "user_ops_hxc_dashboard_snapshot" in by_external_userid["wm_hxc_member_unused"]["registration_source"]
    assert by_external_userid["wm_hxc_member_used"]["has_real_usage"] is True
    assert by_external_userid["wm_hxc_not_member"]["is_member"] is False
    assert [row["external_userid"] for row in target_rows] == ["wm_hxc_member_unused"]
