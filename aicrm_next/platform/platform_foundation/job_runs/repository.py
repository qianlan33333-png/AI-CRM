from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from .port import FinishJobRunRequest, StartJobRunRequest


def _json(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


class PostgresJobRunLedgerRepository:
    """Canonical sync_runs writer; commit ownership remains with each caller."""

    def start_dbapi(self, executor: Any, *, request: StartJobRunRequest) -> int:
        row = executor.execute(
            """
            INSERT INTO sync_runs (
                status, start_time, end_time, owner_userid, cursor, raw_response, created_at
            )
            VALUES ('running', %s, NULLIF(%s::text, '')::timestamptz, %s, %s, COALESCE(%s::jsonb, '{}'::jsonb), CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (
                request.start_time,
                request.end_time,
                request.owner_userid,
                request.cursor,
                _json(request.raw_response),
            ),
        ).fetchone()
        return int((row or {}).get("id") or 0)

    def finish_dbapi(self, executor: Any, run_id: int, *, request: FinishJobRunRequest) -> None:
        executor.execute(
            """
            UPDATE sync_runs
            SET status = %s,
                end_time = COALESCE(NULLIF(%s::text, '')::timestamptz, end_time),
                fetched_count = %s,
                inserted_count = %s,
                raw_response = COALESCE(%s::jsonb, raw_response),
                error_message = %s,
                finished_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                request.status,
                request.end_time,
                int(request.fetched_count),
                int(request.inserted_count),
                _json(request.raw_response),
                request.error_message,
                int(run_id),
            ),
        )

    def start_sqlalchemy(self, connection: Any, *, request: StartJobRunRequest) -> int:
        run_id = connection.execute(
            text(
                """
                INSERT INTO sync_runs (
                    status, start_time, end_time, owner_userid, cursor, raw_response, created_at
                )
                VALUES (
                    'running', CAST(:start_time AS timestamptz),
                    CAST(NULLIF(:end_time, '') AS timestamptz), :owner_userid, :cursor,
                    COALESCE(CAST(:raw_response AS jsonb), '{}'::jsonb), CURRENT_TIMESTAMP
                )
                RETURNING id
                """
            ),
            {
                "start_time": request.start_time,
                "end_time": request.end_time,
                "owner_userid": request.owner_userid,
                "cursor": request.cursor,
                "raw_response": _json(request.raw_response),
            },
        ).scalar_one_or_none()
        return int(run_id or 0)

    def finish_sqlalchemy(self, connection: Any, run_id: int, *, request: FinishJobRunRequest) -> None:
        connection.execute(
            text(
                """
                UPDATE sync_runs
                SET status = :status,
                    end_time = COALESCE(CAST(NULLIF(:end_time, '') AS timestamptz), end_time),
                    fetched_count = :fetched_count,
                    inserted_count = :inserted_count,
                    raw_response = COALESCE(CAST(:raw_response AS jsonb), raw_response),
                    error_message = :error_message,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = :run_id
                """
            ),
            {
                "status": request.status,
                "end_time": request.end_time,
                "fetched_count": int(request.fetched_count),
                "inserted_count": int(request.inserted_count),
                "raw_response": _json(request.raw_response),
                "error_message": request.error_message,
                "run_id": int(run_id),
            },
        )


__all__ = ["PostgresJobRunLedgerRepository"]
