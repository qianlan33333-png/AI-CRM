from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping, Protocol

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory

from .domain import normalize_identity_request
from .dto import ResolvePersonIdentityRequest
from .resolver import resolve_identities_batch_with_sqlalchemy


class CampaignAdmissionPort(Protocol):
    def evaluate(
        self,
        inputs: Iterable[Mapping[str, Any]],
        *,
        owner_userid: str,
        week_started_at: datetime,
        weekly_limit: int,
    ) -> dict[str, dict[str, Any]]: ...


class PostgresCampaignAdmissionPort:
    """Batch identity, current-follow, DND and frequency boundary."""

    def evaluate(
        self,
        inputs: Iterable[Mapping[str, Any]],
        *,
        owner_userid: str,
        week_started_at: datetime,
        weekly_limit: int,
    ) -> dict[str, dict[str, Any]]:
        source = [dict(item) for item in inputs]
        normalized_mobile_by_key: dict[str, str] = {}
        for item in source:
            normalized = normalize_identity_request(
                ResolvePersonIdentityRequest(
                    external_userid=str(item.get("external_userid") or "") or None,
                    unionid=str(item.get("unionid") or "") or None,
                    mobile=str(item.get("mobile") or "") or None,
                )
            )
            normalized_mobile_by_key[str(item.get("row_key") or "")] = str(normalized.mobile or "")
        Session = get_session_factory()
        with Session() as session:
            resolutions = resolve_identities_batch_with_sqlalchemy(session, source)
            resolved = {
                key: value.identity
                for key, value in resolutions.items()
                if value.status == "resolved" and value.identity is not None
            }
            external_userids = sorted(
                {str(item.external_userid or "") for item in resolved.values() if item.external_userid}
            )
            unionids = sorted({str(item.unionid or "") for item in resolved.values() if item.unionid})
            follow_rows = (
                session.execute(
                    text(
                        """
                        SELECT external_userid, user_id
                        FROM wecom_external_contact_follow_users
                        WHERE external_userid = ANY(:external_userids)
                          AND COALESCE(relation_status, 'active') = 'active'
                        """
                    ),
                    {"external_userids": external_userids},
                ).mappings().all()
                if external_userids
                else []
            )
            follows = {
                (str(row.get("external_userid") or ""), str(row.get("user_id") or ""))
                for row in follow_rows
            }
            dnd_rows = (
                session.execute(
                    text(
                        """
                        SELECT DISTINCT unionid
                        FROM user_ops_do_not_disturb_next
                        WHERE unionid = ANY(:unionids)
                          AND is_active = TRUE
                        """
                    ),
                    {"unionids": unionids},
                ).mappings().all()
                if unionids
                else []
            )
            dnd_unionids = {str(row.get("unionid") or "") for row in dnd_rows}
            frequency_rows = (
                session.execute(
                    text(
                        """
                        SELECT target.unionid, COUNT(*)::integer AS touch_count
                        FROM broadcast_jobs job
                        CROSS JOIN LATERAL jsonb_array_elements_text(
                            COALESCE(job.target_unionids_json, '[]'::jsonb)
                        ) AS target(unionid)
                        WHERE job.status = 'sent'
                          AND job.created_at >= :week_started_at
                          AND target.unionid = ANY(:unionids)
                        GROUP BY target.unionid
                        """
                    ),
                    {"unionids": unionids, "week_started_at": week_started_at},
                ).mappings().all()
                if unionids
                else []
            )
        frequency = {
            str(row.get("unionid") or ""): int(row.get("touch_count") or 0)
            for row in frequency_rows
        }
        result: dict[str, dict[str, Any]] = {}
        for item in source:
            row_key = str(item.get("row_key") or "").strip()
            resolution = resolutions[row_key]
            if resolution.status != "resolved" or resolution.identity is None:
                result[row_key] = {
                    "identity_status": "identity_conflict" if resolution.status == "conflict" else "unmatched",
                    "policy_status": "pending",
                    "reason_code": resolution.reason or "identity_not_found",
                    "identity_mobile_normalized": normalized_mobile_by_key.get(row_key, ""),
                }
                continue
            identity = resolution.identity
            unionid = str(identity.unionid or "")
            external_userid = str(identity.external_userid or "")
            policy_status = "eligible"
            reason_code = "eligible"
            if (external_userid, owner_userid) not in follows:
                policy_status = "not_following"
                reason_code = "owner_current_follow_missing"
            elif unionid in dnd_unionids:
                policy_status = "dnd"
                reason_code = "do_not_disturb"
            elif int(weekly_limit) >= 0 and frequency.get(unionid, 0) >= int(weekly_limit):
                policy_status = "frequency_capped"
                reason_code = "weekly_private_message_cap"
            result[row_key] = {
                "identity_status": "resolved",
                "policy_status": policy_status,
                "reason_code": reason_code,
                "resolved_unionid": unionid,
                "resolved_external_userid": external_userid,
                "resolved_owner_userid": owner_userid if policy_status != "not_following" else "",
                "weekly_touch_count": frequency.get(unionid, 0),
                "identity_mobile_normalized": normalized_mobile_by_key.get(row_key, ""),
            }
        return result


def build_campaign_admission_port() -> CampaignAdmissionPort:
    return PostgresCampaignAdmissionPort()


__all__ = [
    "CampaignAdmissionPort",
    "PostgresCampaignAdmissionPort",
    "build_campaign_admission_port",
]
