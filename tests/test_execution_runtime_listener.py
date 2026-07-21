from __future__ import annotations

import json

import pytest

from aicrm_next.platform_foundation.execution_runtime.listener import (
    QUEUE_WAKE_CHANNEL,
    PostgresQueueWakeListener,
    QueueWakeHint,
    listener_database_url,
    parse_wake_hint,
)


def test_wake_hint_accepts_only_queue_kind_and_lane() -> None:
    payload = json.dumps(
        {
            "queue_kind": "external_effect",
            "lane": "wecom_interactive",
            "phone": "13800000000",
            "secret": "must-not-leak",
        }
    )

    assert parse_wake_hint(payload) == QueueWakeHint(
        queue_kind="external_effect",
        lane="wecom_interactive",
    )


@pytest.mark.parametrize("payload", ("", "not-json", "[]", '{"queue_kind":""}'))
def test_invalid_wake_payload_becomes_generic_dirty_hint(payload: str) -> None:
    assert parse_wake_hint(payload) == QueueWakeHint(queue_kind="all", lane="")


def test_listener_database_url_requires_dedicated_url_in_production(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://pooled.example/aicrm")
    monkeypatch.delenv("AICRM_LISTENER_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="AICRM_LISTENER_DATABASE_URL"):
        listener_database_url()


def test_listener_database_url_allows_database_fallback_outside_production(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://postgres@db/aicrm")
    monkeypatch.delenv("AICRM_LISTENER_DATABASE_URL", raising=False)

    assert listener_database_url() == "postgresql://postgres@db/aicrm"


def test_listener_uses_autocommit_direct_connection_and_listens_before_waiting() -> None:
    calls: list[object] = []

    class FakeCursor:
        def execute(self, statement: str) -> None:
            calls.append(statement)

        def close(self) -> None:
            calls.append("cursor.close")

    class FakeConnection:
        def cursor(self):
            calls.append("cursor")
            return FakeCursor()

        def notifies(self, *, timeout: float, stop_after: int):
            calls.append(("notifies", timeout, stop_after))
            return iter(())

        def close(self) -> None:
            calls.append("connection.close")

    def connect(url: str, *, autocommit: bool, application_name: str):
        calls.append(("connect", url, autocommit, application_name))
        return FakeConnection()

    listener = PostgresQueueWakeListener(
        "postgresql://postgres@db/aicrm",
        connect=connect,
    )
    listener.connect()
    assert listener.wait(timeout_seconds=0.25) is None
    listener.close()

    assert calls[0] == (
        "connect",
        "postgresql://postgres@db/aicrm",
        True,
        "aicrm-queue-listener",
    )
    assert f'LISTEN "{QUEUE_WAKE_CHANNEL}"' in calls
    assert calls.index(f'LISTEN "{QUEUE_WAKE_CHANNEL}"') < calls.index(("notifies", 0.25, 1))
