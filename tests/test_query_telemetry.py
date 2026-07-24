from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import text

from aicrm_next.main import _log_request_query_summary, create_app, query_telemetry_logger
from aicrm_next.shared import db_session
from aicrm_next.shared.query_telemetry import (
    InstrumentedDbApiCursor,
    begin_query,
    finish_query,
    query_fingerprint,
    request_query_telemetry_scope,
)


@pytest.fixture(autouse=True)
def reset_engine_cache():
    db_session.reset_engine_cache_for_tests()
    yield
    db_session.reset_engine_cache_for_tests()


def test_query_fingerprint_normalizes_whitespace_without_values() -> None:
    first = query_fingerprint("SELECT *\nFROM customer WHERE unionid = %s")
    second = query_fingerprint("  SELECT  * FROM customer WHERE unionid = %s  ")

    assert first == second
    assert len(first) == 16
    assert "customer" not in first


def test_query_fingerprint_discards_inline_literals_and_comments() -> None:
    first = query_fingerprint(
        "SELECT * FROM customer WHERE external_userid = 'private-first' "
        "AND score > 10 /* private-first-comment */"
    )
    second = query_fingerprint(
        "SELECT * FROM customer WHERE external_userid = 'private-second' "
        "AND score > 99 /* private-second-comment */"
    )

    assert first == second


def test_query_fingerprint_discards_postgres_dollar_quoted_literals() -> None:
    first = query_fingerprint("SELECT $private$first-secret$private$::text")
    second = query_fingerprint("SELECT $private$second-secret$private$::text")

    assert first == second


def test_query_fingerprint_renders_psycopg_composed_shape_without_literal_values() -> None:
    first = query_fingerprint(
        sql.SQL("SELECT * FROM {} WHERE unionid = {}").format(
            sql.Identifier("customer_snapshot"),
            sql.Literal("private-first"),
        )
    )
    second = query_fingerprint(
        sql.SQL("SELECT * FROM {} WHERE unionid = {}").format(
            sql.Identifier("customer_snapshot"),
            sql.Literal("private-second"),
        )
    )
    other_table = query_fingerprint(
        sql.SQL("SELECT * FROM {} WHERE unionid = {}").format(
            sql.Identifier("customer_timeline"),
            sql.Literal("private-first"),
        )
    )

    assert first == second
    assert first != other_table


def test_request_scope_aggregates_fingerprints_and_caps_unique_shapes() -> None:
    with request_query_telemetry_scope() as telemetry:
        for _ in range(2):
            execution = begin_query("SELECT * FROM customer WHERE unionid = %s")
            finish_query(execution)
        for index in range(40):
            execution = begin_query(f"SELECT column_{index} FROM projection")
            finish_query(execution)

    snapshot = telemetry.snapshot()
    assert snapshot.query_count == 42
    assert snapshot.failed_query_count == 0
    assert len(snapshot.fingerprints) == 32
    assert snapshot.fingerprint_overflow_count == 9
    assert max(calls for _, calls in snapshot.fingerprints) == 2


def test_sqlalchemy_observer_counts_calls_not_binding_values(tmp_path) -> None:
    engine = db_session.get_engine(f"sqlite:///{tmp_path / 'telemetry.db'}")

    with request_query_telemetry_scope() as telemetry:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT :customer_value"), {"customer_value": "first-secret"}).scalar() == "first-secret"
            assert connection.execute(text("SELECT :customer_value"), {"customer_value": "second-secret"}).scalar() == "second-secret"

    snapshot = telemetry.snapshot()
    assert snapshot.query_count == 2
    assert snapshot.failed_query_count == 0
    assert len(snapshot.fingerprints) == 1
    assert snapshot.fingerprints[0][1] == 2
    assert "first-secret" not in str(snapshot)
    assert "second-secret" not in str(snapshot)


def test_sqlalchemy_observer_marks_failed_query(tmp_path) -> None:
    engine = db_session.get_engine(f"sqlite:///{tmp_path / 'failed.db'}")

    with request_query_telemetry_scope() as telemetry:
        with engine.connect() as connection:
            with pytest.raises(Exception):
                connection.exec_driver_sql("SELECT * FROM table_that_does_not_exist")

    snapshot = telemetry.snapshot()
    assert snapshot.query_count == 1
    assert snapshot.failed_query_count == 1


def test_dbapi_cursor_observer_delegates_without_retaining_parameters() -> None:
    calls: list[tuple[object, object | None]] = []

    class FakeCursor:
        rowcount = 1

        def execute(self, statement, parameters=None):
            calls.append((statement, parameters))
            return self

        def fetchone(self):
            return {"ok": True}

    with request_query_telemetry_scope() as telemetry:
        cursor = InstrumentedDbApiCursor(FakeCursor())
        result = cursor.execute("SELECT * FROM customer WHERE unionid = %s", ("private-unionid",))

    assert result is cursor
    assert result.fetchone() == {"ok": True}
    assert calls == [("SELECT * FROM customer WHERE unionid = %s", ("private-unionid",))]
    snapshot = telemetry.snapshot()
    assert snapshot.query_count == 1
    assert "private-unionid" not in str(snapshot)


def _render_logged_summary(log_info) -> str:
    message, payload = log_info.call_args.args
    return message % payload


def test_known_route_log_uses_template_and_excludes_query_parameters() -> None:
    private_value = "wmb-private-external-userid"
    client = TestClient(create_app(), raise_server_exceptions=False)

    with patch.object(query_telemetry_logger, "info") as log_info:
        response = client.get(f"/health?external_userid={private_value}")

    assert response.status_code == 200
    log_info.assert_called_once()
    logged_summary = _render_logged_summary(log_info)
    payload = json.loads(logged_summary.split("aicrm request query summary ", 1)[1])
    assert payload["event"] == "aicrm_request_query_summary"
    assert payload["method"] == "GET"
    assert payload["route"] == "/health"
    assert payload["capability"] == "health_read"
    assert payload["status_code"] == 200
    assert isinstance(payload["query_count"], int)
    assert private_value not in logged_summary


def test_log_discards_sql_text_and_business_values() -> None:
    private_sql = "SELECT unionid FROM customer WHERE external_userid = %s"
    private_value = "wmb-private-external-userid"
    request = SimpleNamespace(
        method="GET",
        state=SimpleNamespace(
            route_policy=SimpleNamespace(
                path="/api/sidebar/v2/timeline",
                route_name="sidebar_timeline",
                capability="sidebar_customer_read",
            )
        ),
    )

    with request_query_telemetry_scope() as telemetry:
        execution = begin_query(private_sql)
        finish_query(execution)
        with patch.object(query_telemetry_logger, "info") as log_info:
            _log_request_query_summary(
                request=request,
                status_code=200,
                request_duration_ms=12.5,
                query_telemetry=telemetry,
            )

    logged_summary = _render_logged_summary(log_info)
    assert private_sql not in logged_summary
    assert private_value not in logged_summary
    assert "external_userid = %s" not in logged_summary
    assert query_fingerprint(private_sql) in logged_summary
