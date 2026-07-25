from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import text

from aicrm_next.ai_audience_ops import hxc_projection as projection_module
from aicrm_next.ai_audience_ops.hxc_projection import (
    HXC_PROJECTION_ADVISORY_LOCK_KEY,
    HxcMemberUsageProjectionService,
)
from aicrm_next.ai_audience_ops.hxc_projection_sql import (
    HXC_FULL_PROJECTION_INSERT_SQL,
    HXC_FULL_PROJECTION_SELECT_SQL,
    HXC_OPTIONAL_SOURCE_REQUIRED_COLUMNS,
    build_hxc_full_projection_select_sql,
)
from aicrm_next.shared.db_session import get_session_factory
from scripts.ops import refresh_ai_audience_hxc_projection


def test_hxc_projection_sql_normalizes_identity_before_source_aggregation() -> None:
    lowered = HXC_FULL_PROJECTION_SELECT_SQL.lower()
    insert_columns = HXC_FULL_PROJECTION_INSERT_SQL.split("SELECT :generation", 1)[0]

    assert "with contact_rows as materialized" in lowered
    assert "contact_mobile as materialized" in lowered
    assert "join contacts c on c.unionid" in lowered
    assert "join contact_mobile cm on cm.mobile_hash" in lowered
    assert not re.search(r"\b(?:join|on)\b[^\n]{0,240}\bor\b", lowered)
    assert "external_userid" not in insert_columns
    assert "person_id" not in insert_columns
    all_sources_sql = build_hxc_full_projection_select_sql(
        set(HXC_OPTIONAL_SOURCE_REQUIRED_COLUMNS)
    ).lower()
    assert "nullif(j.finished_at, '')::timestamptz" in all_sources_sql
    assert "new_version_user_subscriptions" in all_sources_sql
    assert "md5(s.phone::text)" in all_sources_sql
    assert "automation_laohuang_chat_job" not in lowered
    assert "new_version_user_subscriptions" not in lowered
    assert "select *" not in lowered


def _seed_projection_sources() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO people (id, mobile, third_party_user_id, updated_at)
                VALUES
                    (99701, '13910000001', 'wm_projection_unused', CURRENT_TIMESTAMP),
                    (99702, '13910000002', 'wm_projection_used', CURRENT_TIMESTAMP),
                    (99703, '13910000003', 'wm_projection_non_member', CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO external_contact_bindings (
                    external_userid, person_id, first_owner_userid,
                    last_owner_userid, updated_at
                )
                VALUES
                    ('wm_projection_unused', '99701', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP),
                    ('wm_projection_used', '99702', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP),
                    ('wm_projection_non_member', '99703', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO wecom_external_contact_identity_map (
                    external_userid, unionid, follow_user_userid,
                    name, status, updated_at
                )
                VALUES
                    ('wm_projection_unused', 'union_projection_unused', 'HuangYouCan', '会员未使用', 'active', CURRENT_TIMESTAMP),
                    ('wm_projection_used', 'union_projection_used', 'HuangYouCan', '会员已使用', 'active', CURRENT_TIMESTAMP),
                    ('wm_projection_non_member', 'union_projection_non_member', 'HuangYouCan', '非会员', 'active', CURRENT_TIMESTAMP)
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
                        '13910000001', '13910000001', 'union_projection_unused', 'HuangYouCan',
                        true, true, 'member_no_usage', CURRENT_TIMESTAMP - interval '3 days',
                        'premium', 'active', CURRENT_TIMESTAMP + interval '30 days',
                        0, 0, 0, NULL, CURRENT_TIMESTAMP
                    ),
                    (
                        '13910000002', '13910000002', 'union_projection_used', 'HuangYouCan',
                        true, true, 'member_used', CURRENT_TIMESTAMP - interval '3 days',
                        'premium', 'active', CURRENT_TIMESTAMP + interval '30 days',
                        1, 1, 2, CURRENT_TIMESTAMP - interval '1 day', CURRENT_TIMESTAMP
                    ),
                    (
                        '13910000003', '13910000003', 'union_projection_non_member', 'HuangYouCan',
                        false, true, 'registered_no_member', CURRENT_TIMESTAMP - interval '3 days',
                        '', '', NULL,
                        0, 0, 0, NULL, CURRENT_TIMESTAMP
                    )
                """
            )
        )
        session.commit()


@pytest.mark.usefixtures("next_pg_schema")
def test_hxc_projection_full_refresh_builds_and_atomically_switches_generation() -> None:
    _seed_projection_sources()
    service = HxcMemberUsageProjectionService()

    first = service.refresh_full()

    assert first["ok"] is True
    assert first["generation"] == 1
    assert first["projected_row_count"] == 3
    assert first["real_external_call_executed"] is False
    assert first["status"]["status"] == "ready"
    assert first["status"]["source_watermarks"].keys() == {"full_calibration_at"}

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.execute(
            text(
                """
                SELECT unionid, is_member, is_registered, has_real_usage,
                       membership_source, registration_source, usage_source
                FROM ai_audience_hxc_member_usage_projection
                WHERE generation = 1
                ORDER BY unionid
                """
            )
        ).mappings().all()
        session.execute(
            text(
                """
                UPDATE user_ops_hxc_dashboard_snapshot
                SET hxc_member_hit = TRUE,
                    membership_status = 'active',
                    membership_end_at = CURRENT_TIMESTAMP + interval '30 days',
                    refreshed_at = CURRENT_TIMESTAMP
                WHERE unionid = 'union_projection_non_member'
                """
            )
        )
        session.commit()

    by_unionid = {str(row["unionid"]): row for row in rows}
    assert by_unionid["union_projection_unused"]["is_member"] is True
    assert by_unionid["union_projection_unused"]["is_registered"] is True
    assert by_unionid["union_projection_unused"]["has_real_usage"] is False
    assert by_unionid["union_projection_used"]["has_real_usage"] is True
    assert by_unionid["union_projection_non_member"]["is_member"] is False

    second = service.refresh_full()

    assert second["ok"] is True
    assert second["generation"] == 2
    assert second["projected_row_count"] == 3
    with session_factory() as session:
        generations = session.execute(
            text(
                """
                SELECT generation, COUNT(*) AS row_count
                FROM ai_audience_hxc_member_usage_projection
                GROUP BY generation
                ORDER BY generation
                """
            )
        ).mappings().all()
        states = session.execute(
            text(
                """
                SELECT generation, is_member
                FROM ai_audience_hxc_member_usage_projection
                WHERE unionid = 'union_projection_non_member'
                ORDER BY generation
                """
            )
        ).all()

    assert [(int(row["generation"]), int(row["row_count"])) for row in generations] == [
        (1, 3),
        (2, 3),
    ]
    assert states == [(1, False), (2, True)]


@pytest.mark.usefixtures("next_pg_schema")
def test_hxc_projection_keeps_optional_subscription_membership_semantics() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(
            text(
                """
                CREATE TABLE public.new_version_user_subscriptions (
                    phone TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )
        session.commit()

    try:
        _seed_projection_sources()
        with session_factory() as session:
            session.execute(
                text(
                    """
                    INSERT INTO public.new_version_user_subscriptions (
                        phone, expires_at
                    )
                    VALUES (
                        '13910000003', CURRENT_TIMESTAMP + interval '30 days'
                    )
                    """
                )
            )
            session.commit()

        result = HxcMemberUsageProjectionService().refresh_full()

        assert result["ok"] is True
        with session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT is_member, is_registered, membership_source,
                               registration_source
                        FROM ai_audience_hxc_member_usage_projection
                        WHERE generation = :generation
                          AND unionid = 'union_projection_non_member'
                        """
                    ),
                    {"generation": result["generation"]},
                )
                .mappings()
                .one()
            )

        assert row["is_member"] is True
        assert row["is_registered"] is True
        assert "new_version_user_subscriptions.phone" in row["membership_source"]
        assert "new_version_user_subscriptions.phone" in row["registration_source"]
    finally:
        with session_factory() as session:
            session.execute(text("DROP TABLE public.new_version_user_subscriptions"))
            session.commit()


@pytest.mark.usefixtures("next_pg_schema")
def test_hxc_projection_refresh_failure_keeps_last_ready_generation(monkeypatch) -> None:
    _seed_projection_sources()
    service = HxcMemberUsageProjectionService()
    assert service.refresh_full()["ok"] is True
    monkeypatch.setattr(
        projection_module,
        "build_hxc_full_projection_insert_sql",
        lambda _sources: "SELECT missing_column FROM missing_projection_source",
    )

    failed = service.refresh_full()
    status = service.status()

    assert failed == {
        "ok": False,
        "error_code": "projection_refresh_failed",
        "real_external_call_executed": False,
    }
    assert status["active_generation"] == 1
    assert status["status"] == "ready"
    assert status["projected_row_count"] == 3
    assert status["last_error_code"] == "projection_refresh_failed"


@pytest.mark.usefixtures("next_pg_schema")
def test_hxc_projection_refresh_is_single_flight() -> None:
    session_factory = get_session_factory()
    blocker = session_factory()
    try:
        blocker.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))
                """
            ),
            {"lock_key": HXC_PROJECTION_ADVISORY_LOCK_KEY},
        )

        result = HxcMemberUsageProjectionService().refresh_full()
    finally:
        blocker.rollback()
        blocker.close()

    assert result == {
        "ok": False,
        "error_code": "projection_refresh_busy",
        "real_external_call_executed": False,
    }


def test_hxc_projection_cli_requires_explicit_action(monkeypatch, capsys) -> None:
    class FakeService:
        def status(self):
            return {"active_generation": 7, "status": "ready"}

        def refresh_full(self):
            return {"ok": True, "generation": 8}

    monkeypatch.setattr(
        refresh_ai_audience_hxc_projection,
        "HxcMemberUsageProjectionService",
        FakeService,
    )

    assert refresh_ai_audience_hxc_projection.main(["--status"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload == {
        "ok": True,
        "status": {"active_generation": 7, "status": "ready"},
    }

    assert refresh_ai_audience_hxc_projection.main(["--full"]) == 0
    refresh_payload = json.loads(capsys.readouterr().out)
    assert refresh_payload == {"generation": 8, "ok": True}

    with pytest.raises(SystemExit):
        refresh_ai_audience_hxc_projection.main([])
