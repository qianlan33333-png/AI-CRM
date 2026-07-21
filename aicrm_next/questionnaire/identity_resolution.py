from __future__ import annotations

import json
from typing import Any


def enqueue_questionnaire_identity_resolution(
    conn: Any,
    payload: dict[str, Any],
    *,
    reason: str,
) -> None:
    source_key = (
        _text(payload.get("respondent_key"))
        or _text(payload.get("openid"))
        or _text(payload.get("external_userid"))
        or _text(payload.get("mobile"))
        or f"questionnaire:{int(payload.get('questionnaire_id') or 0)}"
    )
    conn.execute(
        """
        INSERT INTO crm_user_identity_resolution_queue (
            source_type,
            source_key,
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
            'questionnaire_submission',
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            'pending',
            NOW(),
            NOW(),
            NOW(),
            NOW()
        )
        ON CONFLICT (source_type, source_key) WHERE status = 'pending' AND source_type <> '' AND source_key <> ''
        DO UPDATE SET
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
            source_key,
            _text(payload.get("external_userid")),
            _text(payload.get("openid")),
            _text(payload.get("mobile")),
            json.dumps(payload, ensure_ascii=False, default=str),
            _text(reason) or "identity_unresolved",
        ),
    )
    row = conn.execute("SELECT * FROM crm_user_identity_resolution_queue WHERE source_type = 'questionnaire_submission' AND source_key = %s AND status = 'pending'", (source_key,)).fetchone()
    if row:
        from aicrm_next.identity_contact.resolution_effects import plan_identity_resolution_effect

        plan_identity_resolution_effect(
            conn,
            dict(row),
            source_route="questionnaire.identity_resolution.enqueue",
        )


def _text(value: Any) -> str:
    return str(value or "").strip()
