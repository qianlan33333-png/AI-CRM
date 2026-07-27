from __future__ import annotations

import logging
from typing import Any

from aicrm_next.customer_tags.local_projection import project_questionnaire_tags
from aicrm_next.platform_foundation.external_effects.continuations import ExternalEffectContinuation
from aicrm_next.platform_foundation.external_effects.models import (
    WECOM_CONTACT_TAG_MARK,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
)
from aicrm_next.shared.safe_logging import safe_log_exception

LOGGER = logging.getLogger(__name__)


def _matches_questionnaire_tag_projection(
    job: ExternalEffectJob,
    _dispatch_result: ExternalEffectDispatchResult,
) -> bool:
    if job.effect_type != WECOM_CONTACT_TAG_MARK or job.business_type != "questionnaire_submission":
        return False
    projection = dict((job.payload_json or {}).get("projection") or {})
    return projection.get("type") == "questionnaire_contact_tags"


def _project_questionnaire_tags_after_success(
    job: ExternalEffectJob,
    _dispatch_result: ExternalEffectDispatchResult,
) -> dict[str, Any]:
    payload = dict(job.payload_json or {})
    try:
        result = project_questionnaire_tags(
            unionid=str(payload.get("target_unionid") or job.target_id or "").strip(),
            external_userid=str(payload.get("external_userid") or "").strip(),
            owner_userid=str(payload.get("follow_user_userid") or "").strip(),
            tag_ids=[str(item or "").strip() for item in payload.get("tag_ids") or [] if str(item or "").strip()],
            source="questionnaire_external_effect_success",
            questionnaire_id=payload.get("questionnaire_id"),
            submission_id=str(payload.get("submission_id") or job.business_id or "").strip(),
            idempotency_key=job.idempotency_key,
        )
    except Exception as exc:
        safe_log_exception(
            LOGGER,
            "questionnaire tag post-success projection failed",
            exc,
            external_effect_job_id=int(job.id or 0),
        )
        return {
            "ok": False,
            "projection_type": "questionnaire_contact_tags",
            "error": str(exc)[:500],
        }
    if not result.get("ok") or not result.get("local_projection_updated"):
        return {
            "ok": False,
            "projection_type": "questionnaire_contact_tags",
            "error": str(result.get("reason") or "questionnaire tag projection did not update"),
            "local_projection_status": str(result.get("local_projection_status") or ""),
        }
    return {
        "ok": True,
        "projection_type": "questionnaire_contact_tags",
        "local_projection_status": "updated",
        "inserted_count": int(result.get("inserted_count") or 0),
        "updated_count": int(result.get("updated_count") or 0),
    }


QUESTIONNAIRE_CONTACT_TAGS_CONTINUATION = ExternalEffectContinuation(
    name="questionnaire_contact_tags",
    matches=_matches_questionnaire_tag_projection,
    run=_project_questionnaire_tags_after_success,
)
