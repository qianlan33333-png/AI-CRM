from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "0150_crm_identity_updated_cursor_index.py"
INDEX_NAME = "idx_crm_user_identity_updated_unionid"


def test_identity_cursor_index_migration_is_concurrent_and_rollback_safe() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'down_revision = "0149_ai_audience_hxc_projection"' in source
    assert "CREATE INDEX CONCURRENTLY" in source
    assert "ON {TABLE_NAME} (updated_at, unionid)" in source
    assert "DROP INDEX" not in source


@pytest.mark.usefixtures("next_pg_schema")
def test_identity_cursor_index_exists_with_the_expected_column_order() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        index_definition = session.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'crm_user_identity'
                  AND indexname = :index_name
                """
            ),
            {"index_name": INDEX_NAME},
        ).scalar_one()

    normalized = " ".join(str(index_definition).lower().split())
    assert "using btree (updated_at, unionid)" in normalized
