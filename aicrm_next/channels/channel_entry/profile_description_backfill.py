from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from aicrm_next.platform.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import (
    WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
    WECOM_PROFILE_UPDATE,
    ExternalEffectService,
)
from aicrm_next.platform.platform_foundation.external_effects.repo import build_external_effect_repository
from aicrm_next.platform.shared.db_session import get_session_factory


PROFILE_DESCRIPTION_BACKFILL_RUN_ID = "wecom-profile-description-empty-v1"
PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE = "wecom_profile_description_backfill_detail"
PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE = "wecom_profile_description_backfill_update"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _target_digest(*values: str) -> str:
    material = "\0".join(_text(value) for value in values)
    return sha256(material.encode("utf-8")).hexdigest()[:32]


def _stage_failure(stage: str, exc: Exception) -> dict[str, Any]:
    exception_type = exc.__class__.__name__.lower()
    sqlstate = _text(getattr(getattr(exc, "orig", None), "sqlstate", ""))
    suffix = f"_{sqlstate.lower()}" if sqlstate else ""
    return {"ok": False, "error": f"profile_backfill_{stage}_failed_{exception_type}{suffix}"}


def is_profile_description_backfill_detail(job: Any) -> bool:
    payload = dict(getattr(job, "payload_json", {}) or {})
    return (
        _text(getattr(job, "effect_type", "")) == WECOM_EXTERNAL_CONTACT_DETAIL_FETCH
        and _text(getattr(job, "business_type", "")) == PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE
        and _text(payload.get("external_userid"))
        and bool(_owner_userids(payload))
    )


def is_profile_description_backfill_settlement(job: Any) -> bool:
    return (
        _text(getattr(job, "effect_type", "")) == WECOM_PROFILE_UPDATE
        and _text(getattr(job, "business_type", "")) == PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE
        and _text(getattr(job, "status", ""))
        in {"succeeded", "failed_terminal", "blocked", "cancelled", "expired", "unknown_after_dispatch"}
    )


def _owner_userids(payload: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            _text(value)
            for value in list(payload.get("owner_userids") or [])
            if _text(value)
        )
    )


def _sync_live_nonempty_descriptions(
    *,
    external_userid: str,
    descriptions_by_owner: dict[str, str],
) -> int:
    nonempty = {owner: description for owner, description in descriptions_by_owner.items() if description}
    if not nonempty:
        return 0
    updated = 0
    with get_session_factory()() as session:
        for owner, description in nonempty.items():
            updated += int(
                session.execute(
                    text(
                        """
                        UPDATE wecom_external_contact_follow_users
                        SET description = :description,
                            raw_follow_user = COALESCE(raw_follow_user, '{}'::jsonb)
                                || jsonb_build_object('description', :description),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE external_userid = :external_userid
                          AND user_id = :owner_userid
                          AND relation_status = 'active'
                          AND BTRIM(COALESCE(description, '')) = ''
                        """
                    ),
                    {
                        "description": description,
                        "external_userid": external_userid,
                        "owner_userid": owner,
                    },
                ).rowcount
                or 0
            )
        session.commit()
    return updated


def plan_empty_profile_description_update(
    *,
    parent_job: Any,
    provider_detail: dict[str, Any],
    external_userid: str,
    owner_userid: str,
    service: ExternalEffectService | None = None,
) -> dict[str, Any]:
    external = _text(external_userid)
    owner = _text(owner_userid)
    follow_user = next(
        (
            dict(item or {})
            for item in list(provider_detail.get("follow_user") or [])
            if _text((item or {}).get("userid")) == owner
        ),
        None,
    )
    if not external or not owner:
        return {"status": "skipped", "reason": "target_identity_missing"}
    if follow_user is None:
        return {"status": "skipped", "reason": "owner_relationship_missing"}
    if _text(follow_user.get("description")):
        return {"status": "skipped", "reason": "live_description_nonempty"}

    run_id = _text(getattr(parent_job, "business_id", "")) or PROFILE_DESCRIPTION_BACKFILL_RUN_ID
    target_digest = _target_digest(external, owner)
    planned = (service or ExternalEffectService()).plan_effect(
        effect_type=WECOM_PROFILE_UPDATE,
        adapter_name="wecom_profile",
        operation="update_description",
        target_type="external_user",
        target_id=external,
        payload={
            "external_userid": external,
            "follow_user_userid": owner,
            "description": external,
        },
        payload_summary={
            "external_userid_present": True,
            "owner_userid_present": True,
            "live_description_empty": True,
            "description_source": "external_userid",
            "backfill_run_id": run_id,
            "target_digest": target_digest,
            "real_external_call_executed": False,
        },
        context=CommandContext(
            actor_id="wecom_profile_description_backfill",
            actor_type="system",
            request_id=run_id,
            trace_id=_text(getattr(parent_job, "trace_id", "")) or f"profile-backfill-{target_digest}",
            source_route="external_effect.completed/profile_description_backfill",
        ),
        business_type=PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE,
        business_id=run_id,
        source_module="aicrm_next.channels.channel_entry.profile_description_backfill",
        source_command_id=str(int(getattr(parent_job, "id", 0) or 0)),
        risk_level="medium",
        execution_mode="execute",
        status="queued",
        priority=300,
        max_attempts=5,
        idempotency_key=f"wecom-profile-description-empty:{target_digest}:v1",
        execution_id=f"exe_profile_description_backfill_{uuid4().hex}",
        parent_execution_id=_text(getattr(parent_job, "execution_id", "")),
        lane="wecom_interactive",
        ordering_key=f"external_user:{external}",
        fairness_key="wecom-profile-description-backfill",
    )
    return {
        "status": "queued",
        "external_effect_job_id": int(planned.get("id") or 0),
        "created": bool(planned.get("created_on_plan")),
    }


def run_profile_description_backfill_detail(job: Any, dispatch_result: Any) -> dict[str, Any]:
    payload = dict(getattr(job, "payload_json", {}) or {})
    provider_detail = dict(getattr(dispatch_result, "provider_result", {}) or {})
    external = _text(payload.get("external_userid"))
    provider_external = _text((provider_detail.get("external_contact") or {}).get("external_userid"))
    attempt_id = _text(getattr(job, "last_attempt_id", ""))
    if not external or not attempt_id:
        return {"ok": False, "error": "profile_backfill_completion_identifiers_missing"}
    if not provider_external or provider_external != external:
        return {"ok": False, "error": "profile_backfill_provider_target_mismatch"}

    candidate_owners = _owner_userids(payload)
    follow_by_owner = {
        _text((item or {}).get("userid")): dict(item or {})
        for item in list(provider_detail.get("follow_user") or [])
        if _text((item or {}).get("userid"))
    }
    live_descriptions = {
        owner: _text(follow_by_owner[owner].get("description"))
        for owner in candidate_owners
        if owner in follow_by_owner
    }
    try:
        projection_refreshed_count = _sync_live_nonempty_descriptions(
            external_userid=external,
            descriptions_by_owner=live_descriptions,
        )
    except Exception as exc:
        return _stage_failure("projection", exc)

    service = ExternalEffectService()
    try:
        planned = [
            plan_empty_profile_description_update(
                parent_job=job,
                provider_detail=provider_detail,
                external_userid=external,
                owner_userid=owner,
                service=service,
            )
            for owner in candidate_owners
        ]
    except Exception as exc:
        return _stage_failure("planning", exc)
    try:
        consumed = build_external_effect_repository().consume_attempt_provider_result(
            attempt_id,
            job_id=int(getattr(job, "id", 0) or 0),
        )
    except Exception as exc:
        return _stage_failure("provider_result_consume", exc)
    return {
        "ok": True,
        "candidate_relation_count": len(candidate_owners),
        "queued_update_count": sum(item.get("status") == "queued" for item in planned),
        "created_update_count": sum(bool(item.get("created")) for item in planned),
        "live_description_nonempty_count": sum(item.get("reason") == "live_description_nonempty" for item in planned),
        "owner_relationship_missing_count": sum(item.get("reason") == "owner_relationship_missing" for item in planned),
        "projection_refreshed_count": projection_refreshed_count,
        "provider_result_consumed": bool(consumed),
        "real_external_call_executed": False,
    }


def settle_profile_description_backfill(job: Any, _dispatch_result: Any) -> dict[str, Any]:
    status = _text(getattr(job, "status", ""))
    if status != "succeeded":
        return {
            "ok": True,
            "projected": False,
            "profile_update_status": status,
            "real_external_call_executed": False,
        }
    payload = dict(getattr(job, "payload_json", {}) or {})
    external = _text(payload.get("external_userid"))
    owner = _text(payload.get("follow_user_userid"))
    description = _text(payload.get("description"))
    if not external or not owner or description != external:
        return {"ok": False, "error": "profile_backfill_settlement_payload_invalid"}
    with get_session_factory()() as session:
        updated = int(
            session.execute(
                text(
                    """
                    UPDATE wecom_external_contact_follow_users
                    SET description = :description,
                        raw_follow_user = COALESCE(raw_follow_user, '{}'::jsonb)
                            || jsonb_build_object('description', :description),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE external_userid = :external_userid
                      AND user_id = :owner_userid
                      AND relation_status = 'active'
                      AND BTRIM(COALESCE(description, '')) = ''
                    """
                ),
                {
                    "description": description,
                    "external_userid": external,
                    "owner_userid": owner,
                },
            ).rowcount
            or 0
        )
        session.commit()
    return {
        "ok": True,
        "projected": updated > 0,
        "projection_updated_count": updated,
        "profile_update_status": status,
        "real_external_call_executed": False,
    }


__all__ = [
    "PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE",
    "PROFILE_DESCRIPTION_BACKFILL_RUN_ID",
    "PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE",
    "is_profile_description_backfill_detail",
    "is_profile_description_backfill_settlement",
    "plan_empty_profile_description_update",
    "run_profile_description_backfill_detail",
    "settle_profile_description_backfill",
]
