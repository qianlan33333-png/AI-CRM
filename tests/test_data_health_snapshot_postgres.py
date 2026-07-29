from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
from sqlalchemy import event

from aicrm_next.insights.data_health.application import data_health_summary
from aicrm_next.insights.data_health.dto import DataHealthCheckResult
from aicrm_next.insights.data_health.snapshot_repository import (
    DataHealthSnapshotRecord,
    DataHealthSnapshotRepository,
)
from aicrm_next.platform.shared.db_session import get_engine


def _snapshot(generation: str, *, captured_at: datetime, status: str) -> DataHealthSnapshotRecord:
    return DataHealthSnapshotRecord(
        generation_id=generation,
        source_release_sha="c" * 40,
        overall_status=status,
        checks=(
            DataHealthCheckResult(
                check_id="snapshot_repository_guard",
                title="Snapshot repository guard",
                status=status,
                severity={"ok": "green", "warn": "yellow", "fail": "red"}[status],
                summary="Aggregate-only test evidence.",
                evidence={"count": 1},
            ),
        ),
        duration_ms=25,
        captured_at=captured_at,
    )


def test_snapshot_repository_atomically_replaces_the_singleton(next_pg_schema) -> None:
    repository = DataHealthSnapshotRepository()
    first_at = datetime(2026, 7, 25, 0, 0, tzinfo=timezone.utc)
    latest_at = first_at + timedelta(minutes=15)
    first = _snapshot("11111111-1111-1111-1111-111111111111", captured_at=first_at, status="warn")
    latest = _snapshot("22222222-2222-2222-2222-222222222222", captured_at=latest_at, status="ok")

    repository.replace_latest(first)
    repository.replace_latest(latest)

    loaded = repository.load_latest()
    assert loaded == latest
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        row = connection.execute("SELECT COUNT(*), MIN(generation_id::text), MAX(check_count) FROM data_health_snapshot").fetchone()
    assert row == (1, latest.generation_id, 1)


def test_online_data_health_summary_uses_exactly_one_snapshot_query(next_pg_schema) -> None:
    del next_pg_schema
    repository = DataHealthSnapshotRepository()
    captured_at = datetime.now(timezone.utc)
    snapshot = _snapshot(
        "33333333-3333-3333-3333-333333333333",
        captured_at=captured_at,
        status="ok",
    )
    repository.replace_latest(snapshot)
    statements: list[str] = []
    engine = get_engine()

    def _record_statement(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(str(statement))

    event.listen(engine, "before_cursor_execute", _record_statement)
    try:
        payload = data_health_summary(
            snapshot_repository=repository,
            now=lambda: captured_at + timedelta(minutes=1),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _record_statement)

    assert payload["ok"] is True
    assert payload["counts"] == {"ok": 1, "warn": 0, "fail": 0, "not_applicable": 0}
    assert len(statements) == 1
    assert "FROM data_health_snapshot" in statements[0]
    for growth_table in (
        "crm_user_identity",
        "questionnaire_submissions",
        "follow_users",
        "contacts",
        "archived_messages",
    ):
        assert growth_table not in statements[0]
