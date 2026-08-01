from __future__ import annotations

import os
from datetime import timezone
from importlib import import_module

import psycopg
from psycopg.rows import dict_row


def _run_upgrade_sql(connection, migration) -> None:
    for statement in migration.NORMALIZE_SYNC_RUNS_SCHEMA_SQL:
        connection.execute(statement)
    connection.execute(migration.RECONCILE_STALE_ARCHIVE_RUNS_SQL)


def test_reconciliation_closes_only_the_exact_stale_archive_signature(next_pg_schema) -> None:
    migration = import_module("migrations.versions.0161_reconcile_archive_job_run_ledger")

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        stale = connection.execute(
            """
            INSERT INTO sync_runs (
                status, start_time, end_time, owner_userid, cursor,
                fetched_count, inserted_count, raw_response, error_message, created_at
            )
            VALUES (
                'running', '2000-01-01 00:00:00+08'::timestamptz,
                '2099-12-31 23:59:59+08'::timestamptz, 'HuangYouCan', '',
                0, 0, '{}'::jsonb, '', CURRENT_TIMESTAMP - INTERVAL '1 hour'
            )
            RETURNING id
            """
        ).fetchone()
        control = connection.execute(
            """
            INSERT INTO sync_runs (
                status, start_time, end_time, owner_userid, cursor,
                fetched_count, inserted_count, raw_response, error_message, created_at
            )
            VALUES (
                'running', '2000-01-01 00:00:00+08'::timestamptz,
                '2099-12-31 23:59:59+08'::timestamptz, 'another-owner', '',
                0, 0, '{}'::jsonb, '', CURRENT_TIMESTAMP - INTERVAL '1 hour'
            )
            RETURNING id
            """
        ).fetchone()

        _run_upgrade_sql(connection, migration)
        rows = connection.execute(
            """
            SELECT id, status, error_message, raw_response, finished_at
            FROM sync_runs
            WHERE id IN (%s, %s)
            ORDER BY id
            """,
            (int(stale["id"]), int(control["id"])),
        ).fetchall()
        connection.rollback()

    assert rows[0]["status"] == "failed"
    assert rows[0]["error_message"] == "job_run_ledger_finish_failed_reconciled"
    assert rows[0]["raw_response"]["reconciled"] is True
    assert rows[0]["raw_response"]["core_sync_outcome"] == "unknown"
    assert rows[0]["finished_at"] is not None
    assert rows[1]["status"] == "running"
    assert rows[1]["finished_at"] is None


def test_upgrade_normalizes_legacy_production_text_columns_before_reconciliation(next_pg_schema) -> None:
    migration = import_module("migrations.versions.0161_reconcile_archive_job_run_ledger")

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        connection.execute("ALTER TABLE sync_runs ALTER COLUMN raw_response DROP DEFAULT")
        connection.execute("ALTER TABLE sync_runs ALTER COLUMN raw_response DROP NOT NULL")
        connection.execute("ALTER TABLE sync_runs ALTER COLUMN start_time TYPE TEXT USING start_time::text")
        connection.execute("ALTER TABLE sync_runs ALTER COLUMN end_time TYPE TEXT USING end_time::text")
        connection.execute("ALTER TABLE sync_runs ALTER COLUMN raw_response TYPE TEXT USING raw_response::text")
        stale = connection.execute(
            """
            INSERT INTO sync_runs (
                status, start_time, end_time, owner_userid, cursor,
                fetched_count, inserted_count, raw_response, error_message, created_at
            )
            VALUES (
                'running', '2000-01-01 00:00:00',
                '2099-12-31 23:59:59+08', 'HuangYouCan', '',
                0, 0, '{}', '', CURRENT_TIMESTAMP - INTERVAL '1 hour'
            )
            RETURNING id
            """
        ).fetchone()
        blank_response = connection.execute(
            """
            INSERT INTO sync_runs (
                status, start_time, end_time, owner_userid, cursor,
                fetched_count, inserted_count, raw_response, error_message, created_at
            )
            VALUES (
                'success', '2026-07-31 09:00:00',
                '2026-07-31T10:00:00+08:00', 'another-owner', '',
                1, 1, '', '', CURRENT_TIMESTAMP
            )
            RETURNING id
            """
        ).fetchone()
        date_only = connection.execute(
            """
            INSERT INTO sync_runs (
                status, start_time, end_time, owner_userid, cursor,
                fetched_count, inserted_count, raw_response, error_message, created_at
            )
            VALUES (
                'success', '2026-08-01', '2026-08-02', 'date-only-owner', '',
                1, 1, '{}', '', CURRENT_TIMESTAMP
            )
            RETURNING id
            """
        ).fetchone()

        _run_upgrade_sql(connection, migration)
        column_rows = connection.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'sync_runs'
              AND column_name IN ('start_time', 'end_time', 'raw_response')
            ORDER BY column_name
            """
        ).fetchall()
        rows = connection.execute(
            """
            SELECT id, status, raw_response, start_time, end_time, finished_at
            FROM sync_runs
            WHERE id IN (%s, %s, %s)
            ORDER BY id
            """,
            (int(stale["id"]), int(blank_response["id"]), int(date_only["id"])),
        ).fetchall()
        connection.rollback()

    columns = {row["column_name"]: row for row in column_rows}
    assert columns["start_time"]["data_type"] == "timestamp with time zone"
    assert columns["end_time"]["data_type"] == "timestamp with time zone"
    assert columns["raw_response"]["data_type"] == "jsonb"
    assert columns["raw_response"]["is_nullable"] == "NO"
    assert "'{}'::jsonb" in columns["raw_response"]["column_default"]
    assert rows[0]["status"] == "failed"
    assert rows[0]["raw_response"]["reconciled"] is True
    assert rows[0]["finished_at"] is not None
    assert rows[1]["status"] == "success"
    assert rows[1]["raw_response"] == {}
    assert rows[1]["start_time"].astimezone(timezone.utc).isoformat() == "2026-07-31T01:00:00+00:00"
    assert rows[1]["end_time"].astimezone(timezone.utc).isoformat() == "2026-07-31T02:00:00+00:00"
    assert rows[2]["start_time"].astimezone(timezone.utc).isoformat() == "2026-07-31T16:00:00+00:00"
    assert rows[2]["end_time"].astimezone(timezone.utc).isoformat() == "2026-08-01T16:00:00+00:00"
