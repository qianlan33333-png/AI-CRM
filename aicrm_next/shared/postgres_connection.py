from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from aicrm_next.shared.db_session import connect_raw_postgres
from aicrm_next.shared.query_telemetry import begin_query, finish_query
from aicrm_next.shared.runtime import raw_database_url

_scoped_db: ContextVar["PostgresConnection | None"] = ContextVar("next_scoped_db", default=None)


def _strip_tz(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _translate_sql(sql: str) -> str:
    return sql.replace("?", "%s")


class PostgresCursor:
    def __init__(self, conn: Any) -> None:
        from psycopg.rows import dict_row

        base_factory = dict_row

        def row_factory(cursor: Any) -> Any:
            maker = base_factory(cursor)

            def row_maker(values: Any) -> dict[str, Any]:
                row = maker(values)
                return {key: _strip_tz(value) for key, value in row.items()}

            return row_maker

        self._conn = conn
        self._cursor = conn.cursor(row_factory=row_factory)
        self.lastrowid: int | None = None

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | dict[str, Any] | None = None) -> "PostgresCursor":
        translated = _translate_sql(str(sql))
        execution = begin_query(translated)
        try:
            if params is None or (hasattr(params, "__len__") and len(params) == 0):
                self._cursor.execute(translated)
            elif isinstance(params, dict):
                self._cursor.execute(translated, params)
            else:
                self._cursor.execute(translated, tuple(params))
        except Exception:
            finish_query(execution, failed=True)
            raise
        finish_query(execution)
        self.lastrowid = None
        if translated.lstrip().upper().startswith("INSERT") and self._cursor.rowcount and self._cursor.rowcount > 0:
            probe = self._conn.cursor()
            try:
                probe.execute("SAVEPOINT _next_lastval_probe")
                try:
                    probe.execute("SELECT lastval()")
                    row = probe.fetchone()
                    self.lastrowid = int(row[0]) if row else None
                    probe.execute("RELEASE SAVEPOINT _next_lastval_probe")
                except Exception:
                    self.lastrowid = None
                    probe.execute("ROLLBACK TO SAVEPOINT _next_lastval_probe")
                    probe.execute("RELEASE SAVEPOINT _next_lastval_probe")
            finally:
                probe.close()
        return self

    def executemany(self, sql: str, seq: list[tuple[Any, ...]] | list[list[Any]]) -> "PostgresCursor":
        translated = _translate_sql(str(sql))
        execution = begin_query(translated)
        try:
            self._cursor.executemany(translated, list(seq))
        except Exception:
            finish_query(execution, failed=True)
            raise
        finish_query(execution)
        return self

    def fetchone(self) -> dict[str, Any] | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return self._cursor.fetchall()

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    @property
    def description(self) -> Any:
        return self._cursor.description

    def close(self) -> None:
        self._cursor.close()


class PostgresConnection:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def cursor(self) -> PostgresCursor:
        return PostgresCursor(self._conn)

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | dict[str, Any] | None = None) -> PostgresCursor:
        return self.cursor().execute(sql, params)

    def executemany(self, sql: str, seq: list[tuple[Any, ...]] | list[list[Any]]) -> Any:
        cursor = self._conn.cursor()
        translated = _translate_sql(str(sql))
        execution = begin_query(translated)
        try:
            cursor.executemany(translated, list(seq))
        except Exception:
            finish_query(execution, failed=True)
            raise
        finish_query(execution)
        return cursor

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def _database_url() -> str:
    return raw_database_url()


def _connect() -> PostgresConnection:
    database_url = _database_url()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for Next automation admission runtime")
    return PostgresConnection(connect_raw_postgres(database_url, autocommit=False))


def get_db() -> PostgresConnection:
    scoped = _scoped_db.get()
    if scoped is not None:
        return scoped
    return _connect()


@contextmanager
def db_session() -> Iterator[PostgresConnection]:
    existing = _scoped_db.get()
    if existing is not None:
        yield existing
        return
    conn = _connect()
    token = _scoped_db.set(conn)
    try:
        yield conn
    finally:
        _scoped_db.reset(token)
        conn.close()
