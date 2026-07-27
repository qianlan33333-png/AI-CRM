from __future__ import annotations

from typing import Any

from aicrm_next.crm.identity_contact.resolution_queue_port import (
    EnqueueIdentityResolutionRequest,
    build_identity_resolution_queue_port,
)


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
    build_identity_resolution_queue_port().enqueue_dbapi(
        conn,
        EnqueueIdentityResolutionRequest(
            source_type="questionnaire_submission",
            source_key=source_key,
            reason=_text(reason) or "identity_unresolved",
            source_route="questionnaire.identity_resolution.enqueue",
            external_userid=_text(payload.get("external_userid")),
            openid=_text(payload.get("openid")),
            mobile=_text(payload.get("mobile")),
            payload_json=dict(payload),
        ),
    )


def _text(value: Any) -> str:
    return str(value or "").strip()
