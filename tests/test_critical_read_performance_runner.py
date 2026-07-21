from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from aicrm_next.questionnaire.repo import PostgresQuestionnaireReadRepository
from tools import check_critical_read_performance


def test_questionnaire_admin_pages_before_aggregating_children() -> None:
    source = inspect.getsource(PostgresQuestionnaireReadRepository._paged_select)

    assert "WITH questionnaire_page AS" in source
    assert source.index("LIMIT %s OFFSET %s") < source.index("LEFT JOIN LATERAL")
    assert "WHERE question.questionnaire_id = q.id" in source
    assert "WHERE submission.questionnaire_id = q.id" in source


def test_questionnaire_admin_clamps_page_size_to_one_hundred() -> None:
    captured: list[tuple[str, tuple]] = []

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return self._rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, params=()):
            captured.append((str(query), params))
            return Result([{"total": 0}]) if "COUNT(*) AS total" in str(query) else Result([])

    repo = PostgresQuestionnaireReadRepository("postgresql://example.invalid/db")
    repo._connect = lambda: Connection()  # type: ignore[method-assign]

    rows, total = repo.list_questionnaires(limit=999, offset=-10)

    assert rows == []
    assert total == 0
    assert captured[1][1] == (100, 0)


def test_performance_runner_uses_existing_next_repository_paths() -> None:
    source = Path(check_critical_read_performance.__file__).read_text(encoding="utf-8")

    assert "SqlAlchemyCustomerReadModelRepository" in source
    assert "SidebarWorkbenchReadModel" in source
    assert "PostgresQuestionnaireReadRepository" in source
    assert "build_jobs_payload" in source
    assert "SQLPushCenterReadModel" in source
    assert "generate_series(1, 95000)" in source
    assert "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)" in source


def test_performance_migration_repairs_contact_mirror_schema_drift() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/0106_critical_read_path_indexes.py"
    ).read_text(encoding="utf-8")

    for column in (
        "raw_profile",
        "first_seen_at",
        "last_seen_at",
        "created_at",
        "corp_id",
        "raw_follow_user",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in source
    assert "ux_wecom_external_contact_identity_map_corp_external" in source
    assert "ux_wecom_external_contact_follow_users_corp_external_user" in source


@pytest.mark.skipif(
    os.getenv("AICRM_RUN_CRITICAL_READ_PERFORMANCE") != "1",
    reason="dedicated PostgreSQL performance job only",
)
def test_critical_read_performance_against_fixed_postgres_dataset(next_pg_schema) -> None:
    database_url = os.environ.get("AICRM_TEST_DATABASE_URL") or os.environ["DATABASE_URL"]

    report = check_critical_read_performance.run(database_url)

    assert report["ok"] is True, report["failures"]
    assert set(report["profiles"]) == {
        "customer_list",
        "sidebar_workbench",
        "questionnaire_admin",
        "admin_jobs",
        "push_center",
    }
