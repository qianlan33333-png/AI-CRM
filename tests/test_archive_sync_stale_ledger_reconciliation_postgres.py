from __future__ import annotations

import os
from importlib import import_module

import psycopg
from psycopg.rows import dict_row


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

        connection.execute(migration.RECONCILE_STALE_ARCHIVE_RUNS_SQL)
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
