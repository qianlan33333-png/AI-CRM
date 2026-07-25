from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from .resolution_queue_port import EnqueueIdentityResolutionRequest


def _text(value: Any) -> str:
    return str(value or "").strip()


def _payload(value: dict[str, Any]) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str, sort_keys=True)


class PostgresIdentityResolutionQueueRepository:
    def enqueue_dbapi(
        self,
        connection: Any,
        request: EnqueueIdentityResolutionRequest,
    ) -> dict[str, Any]:
        self._validate(request)
        row = connection.execute(
            """
            INSERT INTO crm_user_identity_resolution_queue (
                source_type,
                source_key,
                corp_id,
                external_userid,
                openid,
                mobile,
                payload_json,
                reason,
                status,
                first_seen_at,
                last_seen_at,
                created_at,
                updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'pending',
                NOW(), NOW(), NOW(), NOW()
            )
            ON CONFLICT (source_type, source_key)
            WHERE status = 'pending' AND source_type <> '' AND source_key <> ''
            DO UPDATE SET
                corp_id = COALESCE(NULLIF(EXCLUDED.corp_id, ''), crm_user_identity_resolution_queue.corp_id),
                external_userid = COALESCE(NULLIF(EXCLUDED.external_userid, ''), crm_user_identity_resolution_queue.external_userid),
                openid = COALESCE(NULLIF(EXCLUDED.openid, ''), crm_user_identity_resolution_queue.openid),
                mobile = COALESCE(NULLIF(EXCLUDED.mobile, ''), crm_user_identity_resolution_queue.mobile),
                payload_json = crm_user_identity_resolution_queue.payload_json || EXCLUDED.payload_json,
                reason = EXCLUDED.reason,
                last_seen_at = NOW(),
                updated_at = NOW()
            RETURNING *
            """,
            (
                _text(request.source_type),
                _text(request.source_key),
                _text(request.corp_id),
                _text(request.external_userid),
                _text(request.openid),
                _text(request.mobile),
                _payload(request.payload_json),
                _text(request.reason) or "identity_unresolved",
            ),
        ).fetchone()
        return self._plan(connection, row, request)

    def enqueue_sqlalchemy(
        self,
        session: Any,
        request: EnqueueIdentityResolutionRequest,
    ) -> dict[str, Any]:
        self._validate(request)
        row = session.execute(
            text(
                """
                INSERT INTO crm_user_identity_resolution_queue (
                    source_type,
                    source_key,
                    corp_id,
                    external_userid,
                    openid,
                    mobile,
                    payload_json,
                    reason,
                    status,
                    first_seen_at,
                    last_seen_at,
                    created_at,
                    updated_at
                ) VALUES (
                    :source_type, :source_key, :corp_id, :external_userid, :openid, :mobile,
                    CAST(:payload_json AS jsonb), :reason, 'pending',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (source_type, source_key)
                WHERE status = 'pending' AND source_type <> '' AND source_key <> ''
                DO UPDATE SET
                    corp_id = COALESCE(NULLIF(EXCLUDED.corp_id, ''), crm_user_identity_resolution_queue.corp_id),
                    external_userid = COALESCE(NULLIF(EXCLUDED.external_userid, ''), crm_user_identity_resolution_queue.external_userid),
                    openid = COALESCE(NULLIF(EXCLUDED.openid, ''), crm_user_identity_resolution_queue.openid),
                    mobile = COALESCE(NULLIF(EXCLUDED.mobile, ''), crm_user_identity_resolution_queue.mobile),
                    payload_json = crm_user_identity_resolution_queue.payload_json || EXCLUDED.payload_json,
                    reason = EXCLUDED.reason,
                    last_seen_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """
            ),
            {
                "source_type": _text(request.source_type),
                "source_key": _text(request.source_key),
                "corp_id": _text(request.corp_id),
                "external_userid": _text(request.external_userid),
                "openid": _text(request.openid),
                "mobile": _text(request.mobile),
                "payload_json": _payload(request.payload_json),
                "reason": _text(request.reason) or "identity_unresolved",
            },
        ).mappings().one()
        return self._plan(session, row, request)

    @staticmethod
    def _validate(request: EnqueueIdentityResolutionRequest) -> None:
        if not _text(request.source_type):
            raise ValueError("identity resolution source_type is required")
        if not _text(request.source_key):
            raise ValueError("identity resolution source_key is required")
        if not _text(request.source_route):
            raise ValueError("identity resolution source_route is required")

    @staticmethod
    def _plan(
        connection: Any,
        row: Any,
        request: EnqueueIdentityResolutionRequest,
    ) -> dict[str, Any]:
        if not row:
            return {}
        from .resolution_effects import plan_identity_resolution_effect

        return plan_identity_resolution_effect(
            connection,
            dict(row),
            parent_execution_id=_text(request.parent_execution_id),
            source_route=_text(request.source_route),
        )


__all__ = ["PostgresIdentityResolutionQueueRepository"]
