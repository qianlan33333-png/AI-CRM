from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import create_engine, text

from aicrm_next.platform.platform_foundation.job_runs import (
    FinishJobRunRequest,
    StartJobRunRequest,
    build_job_run_ledger_port,
)


def test_job_run_ledger_supports_both_caller_transaction_styles(next_pg_schema) -> None:
    port = build_job_run_ledger_port()
    database_url = os.environ["DATABASE_URL"]

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        dbapi_run_id = port.start_dbapi(
            connection,
            request=StartJobRunRequest(
                start_time="2026-07-26T03:00:00Z",
                end_time="2026-07-26T04:00:00Z",
                owner_userid="archive-owner",
                cursor="archive-cursor",
            ),
        )
        port.finish_dbapi(
            connection,
            dbapi_run_id,
            request=FinishJobRunRequest(
                status="success",
                fetched_count=5,
                inserted_count=4,
                raw_response={"source": "archive"},
            ),
        )
        dbapi_row = connection.execute("SELECT * FROM sync_runs WHERE id = %s", (dbapi_run_id,)).fetchone()
        connection.rollback()

    sqlalchemy_url = database_url
    if sqlalchemy_url.startswith("postgresql://"):
        sqlalchemy_url = "postgresql+psycopg://" + sqlalchemy_url[len("postgresql://") :]
    engine = create_engine(sqlalchemy_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            sqlalchemy_run_id = port.start_sqlalchemy(
                connection,
                request=StartJobRunRequest(
                    start_time="2026-07-26T05:00:00Z",
                    owner_userid="tag-owner",
                    raw_response={"source": "tags"},
                ),
            )
            port.finish_sqlalchemy(
                connection,
                sqlalchemy_run_id,
                request=FinishJobRunRequest(
                    status="success",
                    end_time="2026-07-26T05:01:00Z",
                    fetched_count=2,
                    inserted_count=2,
                    raw_response={"source": "tags", "ok": True},
                ),
            )
            sqlalchemy_row = connection.execute(
                text("SELECT * FROM sync_runs WHERE id = :run_id"),
                {"run_id": sqlalchemy_run_id},
            ).mappings().one()
            transaction.rollback()
    finally:
        engine.dispose()

    assert dbapi_row["status"] == "success"
    assert dbapi_row["end_time"] == datetime(2026, 7, 26, 4, 0, tzinfo=timezone.utc)
    assert dbapi_row["finished_at"] is not None
    assert dbapi_row["cursor"] == "archive-cursor"
    assert dbapi_row["raw_response"] == {"source": "archive"}
    assert sqlalchemy_row["status"] == "success"
    assert sqlalchemy_row["owner_userid"] == "tag-owner"
    assert sqlalchemy_row["raw_response"] == {"ok": True, "source": "tags"}
