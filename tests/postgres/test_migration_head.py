from __future__ import annotations

import psycopg
import pytest

from scripts.ci.select_test_scope import load_inventory


pytestmark = pytest.mark.postgres


def test_empty_postgres_upgrades_to_the_single_current_head(migrated_database_url: str) -> None:
    expected_head = load_inventory()["migration_contract"]["expected_head"]
    with psycopg.connect(migrated_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            heads = sorted(row[0] for row in cursor.fetchall())
    assert heads == [expected_head]
