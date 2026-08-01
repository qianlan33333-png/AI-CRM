from __future__ import annotations

from pathlib import Path

from aicrm_next.extensions.ai.ai_audience_ops.repository import SQLAlchemyAudienceRepository


ROOT = Path(__file__).resolve().parents[1]


def test_ai_audience_admin_member_read_indexes_cover_page_and_contact_lookup() -> None:
    source = (
        ROOT / "migrations" / "versions" / "0163_ai_audience_admin_member_read_indexes.py"
    ).read_text(encoding="utf-8")

    assert "down_revision = \"0162_ai_audience_groups_binding\"" in source
    assert "autocommit_block" in source
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in source
    assert "idx_ai_audience_member_current_admin_page" in source
    assert "package_id," in source
    assert "first_entered_at DESC" in source
    assert "INCLUDE (unionid)" in source
    assert "idx_wecom_identity_map_external_updated" in source
    assert "external_userid," in source
    assert "updated_at DESC" in source
    assert "INCLUDE (name)" in source


def test_admin_ai_audience_member_query_pages_before_identity_enrichment() -> None:
    statements: list[str] = []

    class Result:
        def __init__(self, rows=None, scalar=None):
            self._rows = rows or []
            self._scalar = scalar

        def scalar_one(self):
            return self._scalar

        def mappings(self):
            return self

        def fetchall(self):
            return self._rows

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def execute(self, statement, params):
            sql = str(statement)
            statements.append(sql)
            if "SELECT COUNT(*)" in sql:
                return Result(scalar=2)
            return Result(
                rows=[
                    {
                        "id": 1,
                        "nickname": "浅蓝",
                        "unionid": "union_1",
                        "external_userid": "wm_1",
                        "entered_at": "2026-08-01T15:00:00+08:00",
                    }
                ]
            )

    rows, total = SQLAlchemyAudienceRepository(session_factory=Session).list_admin_members(7)

    assert total == 2
    assert rows[0]["nickname"] == "浅蓝"
    assert len(statements) == 2
    assert "JOIN" not in statements[0]
    assert "COUNT(*) OVER" not in statements[1]
    assert "WITH paged_members AS MATERIALIZED" in statements[1]
    assert statements[1].index("LIMIT :limit OFFSET :offset") < statements[1].index("LEFT JOIN crm_user_identity")
    assert "LEFT JOIN LATERAL" in statements[1]
    assert "FROM wecom_external_contact_identity_map contact" in statements[1]
