from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from aicrm_next.platform.shared.postgres_test_guard import validate_postgres_test_database_url


ROOT = Path(__file__).resolve().parents[2]


def _expected_migration_head() -> str:
    inventory = json.loads((ROOT / "docs/ci/current_behavior_inventory.json").read_text(encoding="utf-8"))
    return str(inventory["migration_contract"]["expected_head"])


@pytest.fixture(scope="session")
def migrated_database_url() -> str:
    raw_url = str(os.environ.get("AICRM_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    safe = validate_postgres_test_database_url(raw_url)
    os.environ["DATABASE_URL"] = safe.raw_url
    os.environ["AICRM_TEST_DATABASE_URL"] = safe.raw_url
    with psycopg.connect(safe.raw_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
            existing = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT to_regclass('public.alembic_version')")
            version_table = cursor.fetchone()[0]
            heads: list[str] = []
            if version_table:
                cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
                heads = [str(row[0]) for row in cursor.fetchall()]
    if existing:
        expected = _expected_migration_head()
        if heads != [expected]:
            pytest.fail(
                "PostgreSQL layer found a non-empty database outside the current migration head: "
                f"expected={[expected]!r}, actual={heads!r}, sample_tables={sorted(existing)[:10]!r}"
            )
    else:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/ops/bootstrap_database.py",
                "--database-url",
                safe.raw_url,
            ],
            text=True,
            capture_output=True,
            timeout=240,
        )
        if completed.returncode:
            pytest.fail("\n".join(part for part in (completed.stdout, completed.stderr) if part))
    return safe.raw_url


@pytest.fixture
def pg_connection(migrated_database_url: str):
    connection = psycopg.connect(migrated_database_url)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()
