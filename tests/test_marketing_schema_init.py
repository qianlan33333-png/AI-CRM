from __future__ import annotations

from aicrm_next.automation.ops_enrollment.repo import InMemoryUserOpsRepository


def test_marketing_user_ops_fixture_repo_has_seeded_read_model() -> None:
    repo = InMemoryUserOpsRepository()

    rows = repo.list_rows()

    assert rows
    assert {"external_userid", "mobile", "owner_userid"} <= set(rows[0])
