from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import json
from typing import Any, Callable
from uuid import UUID

from psycopg.types.json import Jsonb

from .repo import open_identity_connection


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_safe(value if value is not None else {}), ensure_ascii=False)


def _json(value: Any) -> Jsonb:
    return Jsonb(_json_safe(value if value is not None else {}), dumps=_json_dumps)


class PostgresIdentityEventLogRepository:
    """Single storage owner for the external-contact callback audit log."""

    def __init__(self, *, connection_factory: Callable[[], Any] | None = None) -> None:
        self._connection_factory = connection_factory or open_identity_connection

    def log_external_contact_event(
        self,
        *,
        corp_id: str,
        event_type: str,
        change_type: str,
        external_userid: str,
        user_id: str,
        event_time: int,
        event_key: str,
        payload_xml: str,
        payload_json: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connection_factory() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO wecom_external_contact_event_logs (
                    corp_id, event_type, change_type, external_userid, user_id, event_time, event_key,
                    payload_xml, payload_json, process_status, retry_count, error_message, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', 0, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (event_key) DO UPDATE
                SET payload_json = EXCLUDED.payload_json, updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """,
                (
                    _text(corp_id),
                    _text(event_type),
                    _text(change_type),
                    _text(external_userid),
                    _text(user_id),
                    int(event_time or 0),
                    _text(event_key),
                    _text(payload_xml),
                    _json(payload_json),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else {}

    def get_external_contact_event_log(self, event_log_id: int) -> dict[str, Any] | None:
        with self._connection_factory() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM wecom_external_contact_event_logs WHERE id = %s LIMIT 1",
                (int(event_log_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def mark_event_status(self, event_log_id: int, status: str, error_message: str = "") -> None:
        with self._connection_factory() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wecom_external_contact_event_logs
                SET process_status = %s, error_message = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (_text(status), _text(error_message), int(event_log_id)),
            )
            conn.commit()

    def record_identity_sync_result(
        self,
        event_log_id: int,
        *,
        status: str,
        error_code: str = "",
        error_message: str = "",
        response_json: dict[str, Any] | None = None,
    ) -> None:
        with self._connection_factory() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wecom_external_contact_event_logs
                SET identity_sync_status = %s,
                    identity_sync_error_code = %s,
                    identity_sync_error_message = %s,
                    identity_sync_response_json = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    _text(status),
                    _text(error_code),
                    _text(error_message),
                    _json(response_json or {}),
                    int(event_log_id),
                ),
            )
            conn.commit()

    def list_recent_events(self, scene_value: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection_factory() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, change_type, external_userid, user_id, process_status, error_message,
                       identity_sync_status, identity_sync_error_code, identity_sync_error_message,
                       created_at
                FROM wecom_external_contact_event_logs
                WHERE COALESCE(NULLIF(payload_json->>'State', ''), NULLIF(payload_json->>'state', '')) = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (_text(scene_value), max(1, min(int(limit or 20), 100))),
            )
            return [dict(row) for row in cur.fetchall() or []]


__all__ = ["PostgresIdentityEventLogRepository"]
