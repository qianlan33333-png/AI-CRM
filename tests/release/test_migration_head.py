from __future__ import annotations

import psycopg
import pytest

from scripts.ci.select_test_scope import load_inventory


pytestmark = pytest.mark.release


def test_release_database_is_at_the_inventory_head(release_database_url: str) -> None:
    expected = load_inventory()["migration_contract"]["expected_head"]
    with psycopg.connect(release_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            heads = sorted(row[0] for row in cursor.fetchall())
    assert heads == [expected]
