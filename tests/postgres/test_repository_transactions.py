from __future__ import annotations

import psycopg
import pytest

from aicrm_next.automation.ops_enrollment.repo import SqlAlchemyUserOpsRepository
from aicrm_next.platform.shared.postgres_connection import PostgresConnection
from aicrm_next.platform.shared.db_session import get_session_factory, reset_engine_cache_for_tests


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


def test_user_ops_send_record_repairs_a_sequence_behind_existing_ids(migrated_database_url: str) -> None:
    with psycopg.connect(migrated_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE user_ops_send_records_next RESTART IDENTITY")
            cursor.execute(
                """
                INSERT INTO user_ops_send_records_next (id, record_key)
                SELECT value, 'sequence_drift_seed_' || value::text
                FROM generate_series(1, 100) AS value
                """
            )
            cursor.execute("ALTER SEQUENCE user_ops_send_records_next_id_seq RESTART WITH 1")
        connection.commit()

    reset_engine_cache_for_tests()
    session = get_session_factory(migrated_database_url)()
    try:
        repository = SqlAlchemyUserOpsRepository(session)
        created = repository.create_or_get_send_record_by_idempotency(
            idempotency_key="sequence-drift-recovery",
            payload={"operator": "current-postgres-test", "content_preview": "sequence recovery"},
        )
        assert created["id"] == 101
        assert created["idempotency_key"] == "sequence-drift-recovery"
    finally:
        session.close()
        reset_engine_cache_for_tests()
        with psycopg.connect(migrated_database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("TRUNCATE user_ops_send_records_next RESTART IDENTITY")
            connection.commit()
