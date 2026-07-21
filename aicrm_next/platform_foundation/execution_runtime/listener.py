from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from aicrm_next.shared.runtime import production_environment, raw_database_url

from .repository import open_listener_connection


QUEUE_WAKE_CHANNEL = "aicrm_queue_wakeup"
_SAFE_HINT_VALUE = re.compile(r"^[a-z0-9_.-]{1,80}$")


@dataclass(frozen=True)
class QueueWakeHint:
    queue_kind: str = "all"
    lane: str = ""


def _direct_psycopg_url(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.startswith("postgresql+psycopg://"):
        return "postgresql://" + normalized[len("postgresql+psycopg://") :]
    if normalized.startswith("postgres://"):
        return "postgresql://" + normalized[len("postgres://") :]
    return normalized


def listener_database_url() -> str:
    configured = _direct_psycopg_url(os.getenv("AICRM_LISTENER_DATABASE_URL", ""))
    if configured:
        return configured
    if production_environment():
        raise RuntimeError("AICRM_LISTENER_DATABASE_URL is required in production and must use a direct or session-pooled PostgreSQL connection")
    fallback = _direct_psycopg_url(raw_database_url())
    if not fallback:
        raise RuntimeError("PostgreSQL listener database URL is not configured")
    return fallback


def parse_wake_hint(payload: object) -> QueueWakeHint:
    try:
        parsed = json.loads(str(payload or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return QueueWakeHint()
    if not isinstance(parsed, dict):
        return QueueWakeHint()
    queue_kind = str(parsed.get("queue_kind") or "").strip().lower()
    lane = str(parsed.get("lane") or "").strip().lower()
    if not _SAFE_HINT_VALUE.fullmatch(queue_kind):
        return QueueWakeHint()
    if lane and not _SAFE_HINT_VALUE.fullmatch(lane):
        lane = ""
    return QueueWakeHint(queue_kind=queue_kind, lane=lane)


class PostgresQueueWakeListener:
    def __init__(
        self,
        database_url: str | None = None,
        *,
        connect: Callable[..., Any] = open_listener_connection,
        application_name: str = "aicrm-queue-listener",
    ) -> None:
        self._database_url = _direct_psycopg_url(database_url or listener_database_url())
        self._connect = connect
        self._application_name = application_name
        self._connection: Any | None = None
        self.connected = False
        self.last_error = ""

    def connect(self) -> None:
        if self._connection is not None:
            return
        connection = self._connect(
            self._database_url,
            autocommit=True,
            application_name=self._application_name,
        )
        cursor = connection.cursor()
        try:
            cursor.execute(f'LISTEN "{QUEUE_WAKE_CHANNEL}"')
        finally:
            cursor.close()
        self._connection = connection
        self.connected = True
        self.last_error = ""

    def wait(self, *, timeout_seconds: float) -> QueueWakeHint | None:
        if self._connection is None:
            self.connect()
        timeout = max(0.0, float(timeout_seconds))
        try:
            notifications = self._connection.notifies(timeout=timeout, stop_after=1)
            notification = next(iter(notifications), None)
        except Exception as exc:
            self.last_error = exc.__class__.__name__
            self.close()
            raise
        if notification is None:
            return None
        return parse_wake_hint(getattr(notification, "payload", ""))

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        self.connected = False
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
