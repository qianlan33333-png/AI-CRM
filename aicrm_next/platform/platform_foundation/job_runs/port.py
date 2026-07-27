from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StartJobRunRequest:
    start_time: str
    end_time: str = ""
    owner_userid: str = ""
    cursor: str = ""
    raw_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class FinishJobRunRequest:
    status: str
    fetched_count: int
    inserted_count: int
    end_time: str = ""
    raw_response: dict[str, Any] | None = None
    error_message: str = ""


class JobRunLedgerPort(Protocol):
    """Public owner port for the shared job-run audit ledger."""

    def start_dbapi(self, executor: Any, *, request: StartJobRunRequest) -> int: ...

    def finish_dbapi(self, executor: Any, run_id: int, *, request: FinishJobRunRequest) -> None: ...

    def start_sqlalchemy(self, connection: Any, *, request: StartJobRunRequest) -> int: ...

    def finish_sqlalchemy(self, connection: Any, run_id: int, *, request: FinishJobRunRequest) -> None: ...


def build_job_run_ledger_port() -> JobRunLedgerPort:
    from .repository import PostgresJobRunLedgerRepository

    return PostgresJobRunLedgerRepository()


__all__ = [
    "FinishJobRunRequest",
    "JobRunLedgerPort",
    "StartJobRunRequest",
    "build_job_run_ledger_port",
]
