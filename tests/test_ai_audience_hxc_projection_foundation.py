from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from sqlalchemy import text

from aicrm_next.shared.db_session import get_session_factory


MIGRATION = importlib.import_module("migrations.versions.0149_ai_audience_hxc_projection_foundation")


def test_hxc_projection_foundation_is_storage_only_and_does_not_cut_over_view() -> None:
    source = Path(MIGRATION.__file__).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS ai_audience_hxc_member_usage_projection" in source
    assert "CREATE OR REPLACE VIEW audience_read.huangxiaocan_member_usage_status_v1" not in source
    assert "INSERT INTO ai_audience_package" not in source
    assert "SELECT * FROM audience_read.huangxiaocan_member_usage_status_v1" not in source
    assert "DROP TABLE" not in source
    assert "external_userid TEXT" not in source
    assert "person_id BIGINT" not in source


@pytest.mark.usefixtures("next_pg_schema")
def test_hxc_projection_foundation_has_empty_generation_and_query_indexes() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        control = session.execute(
            text(
                """
                SELECT active_generation, status, projected_row_count,
                       source_watermarks_json
                FROM ai_audience_hxc_member_usage_projection_control
                WHERE singleton = TRUE
                """
            )
        ).mappings().one()
        indexes = {
            str(row[0])
            for row in session.execute(
                text(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = 'ai_audience_hxc_member_usage_projection'
                    """
                )
            ).all()
        }

    assert dict(control) == {
        "active_generation": 0,
        "status": "empty",
        "projected_row_count": 0,
        "source_watermarks_json": {},
    }
    assert {
        "ai_audience_hxc_member_usage_projection_pkey",
        "idx_ai_audience_hxc_projection_member_unused",
        "idx_ai_audience_hxc_projection_active_member",
        "idx_ai_audience_hxc_projection_mobile_hash",
    } <= indexes
