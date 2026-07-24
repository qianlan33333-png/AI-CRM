from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from time import perf_counter
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine


_MAX_FINGERPRINTS_PER_REQUEST = 32
_SQL_WHITESPACE = re.compile(r"\s+")
_DOLLAR_QUOTE_START = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


@dataclass(frozen=True)
class QueryExecution:
    telemetry: "RequestQueryTelemetry"
    started_at: float


@dataclass(frozen=True)
class QueryTelemetrySnapshot:
    query_count: int
    failed_query_count: int
    query_duration_ms: float
    fingerprint_overflow_count: int
    fingerprints: tuple[tuple[str, int], ...]


@dataclass
class RequestQueryTelemetry:
    query_count: int = 0
    failed_query_count: int = 0
    query_duration_ms: float = 0.0
    fingerprint_overflow_count: int = 0
    _fingerprints: Counter[str] = field(default_factory=Counter, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def begin(self, statement: object) -> QueryExecution:
        fingerprint = query_fingerprint(statement)
        with self._lock:
            self.query_count += 1
            if fingerprint in self._fingerprints or len(self._fingerprints) < _MAX_FINGERPRINTS_PER_REQUEST:
                self._fingerprints[fingerprint] += 1
            else:
                self.fingerprint_overflow_count += 1
        return QueryExecution(telemetry=self, started_at=perf_counter())

    def finish(self, execution: QueryExecution, *, failed: bool = False) -> None:
        elapsed_ms = max(0.0, (perf_counter() - execution.started_at) * 1000.0)
        with self._lock:
            self.query_duration_ms += elapsed_ms
            if failed:
                self.failed_query_count += 1

    def snapshot(self) -> QueryTelemetrySnapshot:
        with self._lock:
            fingerprints = tuple(sorted(self._fingerprints.items(), key=lambda item: (-item[1], item[0])))
            return QueryTelemetrySnapshot(
                query_count=self.query_count,
                failed_query_count=self.failed_query_count,
                query_duration_ms=round(self.query_duration_ms, 3),
                fingerprint_overflow_count=self.fingerprint_overflow_count,
                fingerprints=fingerprints,
            )


_REQUEST_QUERY_TELEMETRY: ContextVar[RequestQueryTelemetry | None] = ContextVar(
    "aicrm_request_query_telemetry",
    default=None,
)


def query_fingerprint(statement: object) -> str:
    """Return a stable query-shape digest without retaining SQL or binding values."""

    normalized = _normalize_query_shape(_statement_text(statement))
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]


def _statement_text(statement: object) -> str:
    if statement is None:
        return ""
    as_string = getattr(statement, "as_string", None)
    if callable(as_string):
        try:
            rendered = as_string()
        except Exception:
            pass
        else:
            if isinstance(rendered, str):
                return rendered
    return str(statement)


def _normalize_query_shape(statement: str) -> str:
    """Discard literals and comments before hashing a SQL statement shape."""

    output: list[str] = []
    index = 0
    length = len(statement)
    while index < length:
        current = statement[index]

        if current == "'":
            output.append("?")
            index += 1
            while index < length:
                if statement[index] == "'":
                    if index + 1 < length and statement[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                if statement[index] == "\\" and index + 1 < length:
                    index += 2
                    continue
                index += 1
            continue

        if current == "$":
            delimiter_match = _DOLLAR_QUOTE_START.match(statement, index)
            if delimiter_match is not None:
                delimiter = delimiter_match.group(0)
                closing_index = statement.find(delimiter, delimiter_match.end())
                if closing_index >= 0:
                    output.append("?")
                    index = closing_index + len(delimiter)
                    continue

        if current == "-" and index + 1 < length and statement[index + 1] == "-":
            newline_index = statement.find("\n", index + 2)
            output.append(" ")
            index = length if newline_index < 0 else newline_index + 1
            continue

        if current == "/" and index + 1 < length and statement[index + 1] == "*":
            index += 2
            depth = 1
            while index < length and depth > 0:
                if statement.startswith("/*", index):
                    depth += 1
                    index += 2
                elif statement.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            output.append(" ")
            continue

        previous = statement[index - 1] if index > 0 else ""
        if current.isdigit() and not (previous.isalnum() or previous in {"_", "$"}):
            number_end = index + 1
            if current == "0" and number_end < length and statement[number_end] in {"x", "X"}:
                number_end += 1
                while number_end < length and statement[number_end] in "0123456789abcdefABCDEF":
                    number_end += 1
            else:
                while number_end < length and statement[number_end].isdigit():
                    number_end += 1
                if number_end < length and statement[number_end] == ".":
                    number_end += 1
                    while number_end < length and statement[number_end].isdigit():
                        number_end += 1
                if number_end < length and statement[number_end] in {"e", "E"}:
                    exponent_end = number_end + 1
                    if exponent_end < length and statement[exponent_end] in {"+", "-"}:
                        exponent_end += 1
                    exponent_digits = exponent_end
                    while exponent_end < length and statement[exponent_end].isdigit():
                        exponent_end += 1
                    if exponent_end > exponent_digits:
                        number_end = exponent_end
            output.append("?")
            index = number_end
            continue

        output.append(current)
        index += 1

    return _SQL_WHITESPACE.sub(" ", "".join(output)).strip()


def begin_query(statement: object) -> QueryExecution | None:
    telemetry = _REQUEST_QUERY_TELEMETRY.get()
    return telemetry.begin(statement) if telemetry is not None else None


def finish_query(execution: QueryExecution | None, *, failed: bool = False) -> None:
    if execution is not None:
        execution.telemetry.finish(execution, failed=failed)


@contextmanager
def request_query_telemetry_scope() -> Iterator[RequestQueryTelemetry]:
    telemetry = RequestQueryTelemetry()
    token = _REQUEST_QUERY_TELEMETRY.set(telemetry)
    try:
        yield telemetry
    finally:
        _REQUEST_QUERY_TELEMETRY.reset(token)


class InstrumentedDbApiCursor:
    """Delegate a DB-API cursor while observing only statement shapes and timing."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def __enter__(self) -> "InstrumentedDbApiCursor":
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback) -> Any:
        return self._cursor.__exit__(exc_type, exc, traceback)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __iter__(self):
        return iter(self._cursor)

    def execute(self, statement: object, parameters: object | None = None) -> "InstrumentedDbApiCursor":
        execution = begin_query(statement)
        try:
            if parameters is None:
                self._cursor.execute(statement)
            else:
                self._cursor.execute(statement, parameters)
        except Exception:
            finish_query(execution, failed=True)
            raise
        finish_query(execution)
        return self

    def executemany(self, statement: object, parameters: object) -> "InstrumentedDbApiCursor":
        execution = begin_query(statement)
        try:
            self._cursor.executemany(statement, parameters)
        except Exception:
            finish_query(execution, failed=True)
            raise
        finish_query(execution)
        return self


def install_sqlalchemy_query_telemetry(engine: Engine) -> None:
    """Attach per-request observers to one engine without logging SQL text or values."""

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    event.listen(engine, "after_cursor_execute", _after_cursor_execute)
    event.listen(engine, "handle_error", _handle_error)


def _before_cursor_execute(_conn, _cursor, statement, _parameters, context, _executemany) -> None:
    context._aicrm_query_execution = begin_query(statement)


def _after_cursor_execute(_conn, _cursor, _statement, _parameters, context, _executemany) -> None:
    finish_query(getattr(context, "_aicrm_query_execution", None))
    context._aicrm_query_execution = None


def _handle_error(exception_context) -> None:
    context = getattr(exception_context, "execution_context", None)
    if context is None:
        return
    finish_query(getattr(context, "_aicrm_query_execution", None), failed=True)
    context._aicrm_query_execution = None
