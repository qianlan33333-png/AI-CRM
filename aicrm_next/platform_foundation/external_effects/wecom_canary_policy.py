from __future__ import annotations

"""Fail-closed provider-boundary policy for ID validation WeCom canaries.

The durable queue scope decides which rows are eligible.  This module repeats
the decision at the final adapter boundary so a stale worker, an accidental
direct dispatch, or a malformed payload still cannot reach WeCom.
"""

from collections.abc import Iterable
from typing import Any

from aicrm_next.shared.runtime_settings import runtime_csv, runtime_setting
from aicrm_next.shared.wecom_runtime import load_wecom_execution_config


WECOM_PROVIDER_TARGET_POLICY_KEY = "AICRM_WECOM_PROVIDER_TARGET_POLICY"
WECOM_PROVIDER_TARGET_POLICY_BLOCKED = "blocked"
WECOM_PROVIDER_TARGET_POLICY_ALLOWLISTED_CANARY = "allowlisted_canary"
WECOM_ALLOWLISTED_CANARY_SCOPE = "allowlisted_canary"
WECOM_ALLOWED_EXTERNAL_USERIDS_KEY = "AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS"
WECOM_ALLOWED_OWNER_USERIDS_KEY = "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS"
WECOM_ALLOWED_GROUP_WEBHOOK_KEYS_KEY = "AICRM_EXTERNAL_EFFECT_ALLOWED_GROUP_OPS_WEBHOOK_KEYS"
WECOM_ALLOWED_GROUP_CHAT_IDS_KEY = "AICRM_EXTERNAL_EFFECT_ALLOWED_GROUP_CHAT_IDS"
WECOM_ALLOWED_MEDIA_TARGETS_KEY = "AICRM_WECOM_CANARY_ALLOWED_MEDIA_TARGETS"


def _values(items: Iterable[Any]) -> set[str]:
    return {str(item or "").strip() for item in items if str(item or "").strip()}


def _allowlist_error(*, requested: set[str], configured: set[str], code: str) -> str:
    if requested and (not configured or not requested.issubset(configured)):
        return code
    return ""


def wecom_canary_gate_error(
    *,
    payload: dict[str, Any],
    external_userids: Iterable[Any] = (),
    owner_userids: Iterable[Any] = (),
    group_chat_ids: Iterable[Any] = (),
    group_webhook_key: str = "",
    media_target: str = "",
    mention_all: bool = False,
) -> str:
    """Return a stable pre-provider block code, or an empty string when safe."""

    policy = runtime_setting(
        WECOM_PROVIDER_TARGET_POLICY_KEY,
        WECOM_PROVIDER_TARGET_POLICY_BLOCKED,
    ).strip().lower()
    if policy != WECOM_PROVIDER_TARGET_POLICY_ALLOWLISTED_CANARY:
        return "wecom_provider_target_policy_blocked"
    if str(payload.get("execution_scope") or "").strip() != WECOM_ALLOWLISTED_CANARY_SCOPE:
        return "wecom_execution_scope_not_allowlisted_canary"
    if mention_all:
        return "wecom_canary_mention_all_blocked"

    error = _allowlist_error(
        requested=_values(external_userids),
        configured=runtime_csv(WECOM_ALLOWED_EXTERNAL_USERIDS_KEY),
        code="wecom_target_not_allowlisted",
    )
    if error:
        return error
    error = _allowlist_error(
        requested=_values(owner_userids),
        configured=runtime_csv(WECOM_ALLOWED_OWNER_USERIDS_KEY),
        code="wecom_owner_not_allowlisted",
    )
    if error:
        return error
    error = _allowlist_error(
        requested=_values(group_chat_ids),
        configured=runtime_csv(WECOM_ALLOWED_GROUP_CHAT_IDS_KEY),
        code="wecom_group_chat_not_allowlisted",
    )
    if error:
        return error

    webhook_key = str(group_webhook_key or "").strip()
    if webhook_key and webhook_key not in runtime_csv(WECOM_ALLOWED_GROUP_WEBHOOK_KEYS_KEY):
        return "wecom_group_webhook_not_allowlisted"
    normalized_media_target = str(media_target or "").strip()
    if normalized_media_target and normalized_media_target not in runtime_csv(WECOM_ALLOWED_MEDIA_TARGETS_KEY):
        return "wecom_media_target_not_allowlisted"
    return ""


def wecom_canary_policy_snapshot() -> dict[str, Any]:
    """Expose counts and readiness only; never return target identifiers."""

    policy = runtime_setting(
        WECOM_PROVIDER_TARGET_POLICY_KEY,
        WECOM_PROVIDER_TARGET_POLICY_BLOCKED,
    ).strip().lower()
    allowlists = {
        "external_userid": runtime_csv(WECOM_ALLOWED_EXTERNAL_USERIDS_KEY),
        "owner_userid": runtime_csv(WECOM_ALLOWED_OWNER_USERIDS_KEY),
        "group_webhook_key": runtime_csv(WECOM_ALLOWED_GROUP_WEBHOOK_KEYS_KEY),
        "group_chat_id": runtime_csv(WECOM_ALLOWED_GROUP_CHAT_IDS_KEY),
        "media_target": runtime_csv(WECOM_ALLOWED_MEDIA_TARGETS_KEY),
    }
    blocking_reasons: list[str] = []
    if policy != WECOM_PROVIDER_TARGET_POLICY_ALLOWLISTED_CANARY:
        blocking_reasons.append("wecom_provider_target_policy_blocked")
    return {
        "provider_target_policy": policy,
        "required_execution_scope": WECOM_ALLOWLISTED_CANARY_SCOPE,
        "allowlisted_canary_enabled": not blocking_reasons,
        "allowlist_counts": {key: len(value) for key, value in allowlists.items()},
        "blocking_reasons": blocking_reasons,
    }


def wecom_canary_job_gate_error(job: Any, *, authorize_scope: bool = False) -> str:
    """Evaluate a durable WeCom job without exposing identifiers in the result."""

    payload = dict(getattr(job, "payload_json", {}) or {})
    if authorize_scope:
        payload["execution_scope"] = WECOM_ALLOWLISTED_CANARY_SCOPE
    effect_type = str(getattr(job, "effect_type", "") or "").strip()
    default_sender = load_wecom_execution_config().default_sender_userid
    if effect_type == "wecom.message.private.send":
        external_userids = _values(list(payload.get("external_userids") or []))
        owner_userids = _values(
            [default_sender or payload.get("owner_userid") or payload.get("sender")]
        )
        if len(external_userids) != 1:
            return "wecom_canary_single_external_target_required"
        if not owner_userids:
            return "wecom_canary_owner_required"
        error = wecom_canary_gate_error(
            payload=payload,
            external_userids=external_userids,
            owner_userids=owner_userids,
        )
    elif effect_type == "wecom.message.group.send":
        owner_userids = _values(
            [default_sender or payload.get("owner_userid") or payload.get("sender")]
        )
        group_chat_ids = _values(list(payload.get("chat_ids") or []))
        if not owner_userids:
            return "wecom_canary_owner_required"
        if not group_chat_ids:
            return "wecom_canary_group_chat_required"
        error = wecom_canary_gate_error(
            payload=payload,
            owner_userids=owner_userids,
            group_chat_ids=group_chat_ids,
            group_webhook_key=str(payload.get("webhook_key") or ""),
            mention_all=bool(payload.get("mention_all") or payload.get("is_mention_all")),
        )
    elif effect_type in {
        "wecom.welcome_message.send",
        "wecom.contact.tag.mark",
        "wecom.contact.tag.unmark",
        "wecom.profile.update",
    }:
        external_userids = _values([payload.get("external_userid")])
        owner_userids = _values(
            [payload.get("follow_user_userid") or payload.get("userid")]
        )
        if not external_userids:
            return "wecom_canary_external_target_required"
        if not owner_userids:
            return "wecom_canary_owner_required"
        error = wecom_canary_gate_error(
            payload=payload,
            external_userids=external_userids,
            owner_userids=owner_userids,
        )
    elif effect_type == "wecom.external_contact.detail.fetch":
        external_userids = _values([payload.get("external_userid")])
        if not external_userids:
            return "wecom_canary_external_target_required"
        error = wecom_canary_gate_error(
            payload=payload,
            external_userids=external_userids,
        )
    elif effect_type == "wecom.media.upload":
        media_target = str(getattr(job, "target_id", "") or "").strip()
        if not media_target:
            return "wecom_canary_media_target_required"
        error = wecom_canary_gate_error(
            payload=payload,
            media_target=media_target,
        )
    else:
        return "wecom_canary_effect_not_supported"
    if error or authorize_scope:
        return error

    authorization = dict(getattr(job, "payload_summary_json", {}) or {}).get(
        "canary_authorization"
    )
    if not isinstance(authorization, dict):
        return "wecom_canary_authorization_missing"
    if not all(
        str(authorization.get(field) or "").strip()
        for field in ("actor", "reason", "authorized_at")
    ):
        return "wecom_canary_authorization_invalid"
    try:
        authorized_job_id = int(authorization.get("authorized_job_id") or 0)
        authorized_from_version = int(
            authorization.get("authorized_from_version") or 0
        )
        current_job_id = int(getattr(job, "id", 0) or 0)
        current_version = int(getattr(job, "row_version", 0) or 0)
    except (TypeError, ValueError):
        return "wecom_canary_authorization_invalid"
    if (
        authorized_job_id < 1
        or authorized_job_id != current_job_id
        or authorized_from_version < 1
        or current_version < authorized_from_version + 1
    ):
        return "wecom_canary_authorization_invalid"
    if authorization.get("duplicate_risk_confirmed") is not False:
        return "wecom_canary_authorization_invalid"
    return ""


__all__ = [
    "WECOM_ALLOWLISTED_CANARY_SCOPE",
    "WECOM_ALLOWED_EXTERNAL_USERIDS_KEY",
    "WECOM_ALLOWED_GROUP_CHAT_IDS_KEY",
    "WECOM_ALLOWED_GROUP_WEBHOOK_KEYS_KEY",
    "WECOM_ALLOWED_MEDIA_TARGETS_KEY",
    "WECOM_ALLOWED_OWNER_USERIDS_KEY",
    "WECOM_PROVIDER_TARGET_POLICY_ALLOWLISTED_CANARY",
    "WECOM_PROVIDER_TARGET_POLICY_BLOCKED",
    "WECOM_PROVIDER_TARGET_POLICY_KEY",
    "wecom_canary_gate_error",
    "wecom_canary_job_gate_error",
    "wecom_canary_policy_snapshot",
]
