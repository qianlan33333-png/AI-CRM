from __future__ import annotations

import psycopg
import pytest

from aicrm_next.platform.shared.postgres_connection import PostgresConnection


pytestmark = pytest.mark.postgres


def test_postgres_connection_translates_parameters_and_rolls_back(migrated_database_url: str) -> None:
    raw = psycopg.connect(migrated_database_url)
    connection = PostgresConnection(raw)
    try:
        connection.execute("CREATE TEMP TABLE current_transaction_probe (value INTEGER NOT NULL)")
        connection.commit()
        connection.execute("INSERT INTO current_transaction_probe (value) VALUES (?)", (7,))
        connection.rollback()
        row = connection.execute("SELECT COUNT(*) AS count FROM current_transaction_probe").fetchone()
        assert row == {"count": 0}
    finally:
        connection.close()


def test_postgres_connection_commit_is_visible_to_another_session(migrated_database_url: str) -> None:
    first = psycopg.connect(migrated_database_url)
    second = psycopg.connect(migrated_database_url)
    try:
        with first.cursor() as cursor:
            cursor.execute("CREATE TABLE IF NOT EXISTS current_commit_probe (probe_key TEXT PRIMARY KEY)")
            cursor.execute("TRUNCATE current_commit_probe")
            cursor.execute("INSERT INTO current_commit_probe (probe_key) VALUES ('visible')")
        first.commit()
        with second.cursor() as cursor:
            cursor.execute("SELECT probe_key FROM current_commit_probe")
            assert cursor.fetchone()[0] == "visible"
    finally:
        first.rollback()
        second.rollback()
        with first.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS current_commit_probe")
        first.commit()
        first.close()
        second.close()
