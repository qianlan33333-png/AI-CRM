from __future__ import annotations

import json

from aicrm_next.platform.platform_foundation.job_runs import (
    FinishJobRunRequest,
    StartJobRunRequest,
    build_job_run_ledger_port,
)


class _DBAPICursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _DBAPIExecutor:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(statement.split()), tuple(params)))
        return _DBAPICursor({"id": 81})


class _SQLAlchemyResult:
    def scalar_one_or_none(self):
        return 82


class _SQLAlchemyConnection:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).split()), dict(params)))
        return _SQLAlchemyResult()


def test_dbapi_job_run_ledger_preserves_caller_commit_boundary() -> None:
    executor = _DBAPIExecutor()
    port = build_job_run_ledger_port()

    run_id = port.start_dbapi(
        executor,
        request=StartJobRunRequest(
            start_time="2026-07-26T00:00:00Z",
            end_time="2026-07-26T01:00:00Z",
            owner_userid="archive-owner",
            cursor="cursor-81",
        ),
    )
    port.finish_dbapi(
        executor,
        run_id,
        request=FinishJobRunRequest(
            status="success",
            fetched_count=9,
            inserted_count=8,
            raw_response={"ok": True},
        ),
    )

    assert run_id == 81
    assert executor.calls[0][0].startswith("INSERT INTO sync_runs")
    assert executor.calls[1][0].startswith("UPDATE sync_runs")
    assert "NULLIF(%s::text, '')::timestamptz" in executor.calls[0][0]
    assert "COALESCE(NULLIF(%s::text, '')::timestamptz, end_time)" in executor.calls[1][0]
    assert json.loads(executor.calls[1][1][4]) == {"ok": True}
    assert not hasattr(executor, "commit")


def test_sqlalchemy_job_run_ledger_uses_same_owner_contract() -> None:
    connection = _SQLAlchemyConnection()
    port = build_job_run_ledger_port()

    run_id = port.start_sqlalchemy(
        connection,
        request=StartJobRunRequest(
            start_time="2026-07-26T02:00:00Z",
            owner_userid="tag-owner",
            raw_response={"errcode": 0},
        ),
    )
    port.finish_sqlalchemy(
        connection,
        run_id,
        request=FinishJobRunRequest(
            status="success",
            end_time="2026-07-26T02:01:00Z",
            fetched_count=3,
            inserted_count=3,
            raw_response={"ok": True, "sync_run_id": run_id},
        ),
    )

    assert run_id == 82
    assert connection.calls[0][0].startswith("INSERT INTO sync_runs")
    assert connection.calls[1][0].startswith("UPDATE sync_runs")
    assert json.loads(connection.calls[0][1]["raw_response"]) == {"errcode": 0}
    assert connection.calls[1][1]["run_id"] == 82
    assert not hasattr(connection, "commit")
