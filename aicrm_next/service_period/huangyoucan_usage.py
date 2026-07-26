from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, Iterable


MATCHED_HUANGYOUCAN_STATUSES = {"matched_unionid", "matched_mobile"}


def normalized_mobile_md5(value: Any) -> str:
    normalized = "".join(character for character in str(value or "") if character.isdigit())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest() if normalized else ""


def huangyoucan_usage_match_joins(*, unionid_sql: str, mobile_sql: str) -> str:
    """Return the shared local-projection identity resolution joins.

    The expressions are static repository SQL fragments, never request input.
    Unionid wins. Phone is used only when unique, while a unique phone pointing
    to another HuangYouCan account is treated as an identity conflict.
    """

    return f"""
        LEFT JOIN LATERAL (
            SELECT
                CASE
                    WHEN union_candidate.candidate_count = 1
                         AND NOT (
                             mobile_candidate.candidate_count = 1
                             AND mobile_candidate.candidate_id <> union_candidate.candidate_id
                         )
                        THEN union_candidate.candidate_id
                    WHEN union_candidate.candidate_count = 0
                         AND mobile_candidate.candidate_count = 1
                        THEN mobile_candidate.candidate_id
                    ELSE NULL
                END AS huangyoucan_user_id,
                CASE
                    WHEN union_candidate.candidate_count = 1
                         AND mobile_candidate.candidate_count = 1
                         AND mobile_candidate.candidate_id <> union_candidate.candidate_id
                        THEN 'ambiguous'
                    WHEN union_candidate.candidate_count = 1 THEN 'matched_unionid'
                    WHEN union_candidate.candidate_count > 1 THEN 'ambiguous'
                    WHEN mobile_candidate.candidate_count = 1 THEN 'matched_mobile'
                    WHEN mobile_candidate.candidate_count > 1 THEN 'ambiguous'
                    ELSE 'not_found'
                END AS match_status
            FROM LATERAL (
                SELECT COUNT(*) AS candidate_count, MIN(snapshot.huangyoucan_user_id) AS candidate_id
                FROM service_period_huangyoucan_usage_snapshot snapshot
                WHERE NULLIF({unionid_sql}, '') IS NOT NULL
                  AND snapshot.unionid <> ''
                  AND snapshot.unionid = {unionid_sql}
            ) union_candidate
            CROSS JOIN LATERAL (
                SELECT COUNT(*) AS candidate_count, MIN(snapshot.huangyoucan_user_id) AS candidate_id
                FROM service_period_huangyoucan_usage_snapshot snapshot
                WHERE NULLIF(regexp_replace(COALESCE({mobile_sql}, ''), '[^0-9]', '', 'g'), '') IS NOT NULL
                  AND snapshot.mobile_md5 <> ''
                  AND snapshot.mobile_md5 = md5(
                      regexp_replace(COALESCE({mobile_sql}, ''), '[^0-9]', '', 'g')
                  )::CHAR(32)
            ) mobile_candidate
        ) huangyoucan_match ON TRUE
        LEFT JOIN service_period_huangyoucan_usage_snapshot huangyoucan_usage
          ON huangyoucan_usage.huangyoucan_user_id = huangyoucan_match.huangyoucan_user_id
    """


def huangyoucan_usage_select_fields() -> str:
    return """
        huangyoucan_match.match_status AS huangyoucan_match_status,
        huangyoucan_usage.formally_logged_in AS huangyoucan_formally_logged_in,
        huangyoucan_usage.has_token_usage AS huangyoucan_has_token_usage,
        huangyoucan_usage.learning_plan_current AS huangyoucan_learning_plan_current,
        huangyoucan_usage.learning_plan_total AS huangyoucan_learning_plan_total,
        huangyoucan_usage.open_count_7d AS huangyoucan_open_count_7d,
        huangyoucan_usage.last_open_at AS huangyoucan_last_open_at,
        COALESCE(
            huangyoucan_usage.refreshed_at,
            (SELECT MAX(latest_snapshot.refreshed_at) FROM service_period_huangyoucan_usage_snapshot latest_snapshot)
        ) AS huangyoucan_data_refreshed_at
    """.strip()


def public_huangyoucan_usage_fields(row: dict[str, Any]) -> dict[str, Any]:
    match_status = str(row.get("huangyoucan_match_status") or "not_found").strip()
    matched = match_status in MATCHED_HUANGYOUCAN_STATUSES
    current = _optional_int(row.get("huangyoucan_learning_plan_current"))
    total = _optional_int(row.get("huangyoucan_learning_plan_total"))
    progress = None
    if matched and current is not None and total is not None:
        progress = {"current": current, "total": total}
    return {
        "huangyoucan_formally_logged_in": bool(row.get("huangyoucan_formally_logged_in")) if matched else None,
        "huangyoucan_has_token_usage": bool(row.get("huangyoucan_has_token_usage")) if matched else None,
        "huangyoucan_learning_plan_progress": progress,
        "huangyoucan_open_count_7d": int(row.get("huangyoucan_open_count_7d") or 0) if matched else None,
        "huangyoucan_last_open_at": _isoformat(row.get("huangyoucan_last_open_at")) if matched else None,
        "huangyoucan_data_refreshed_at": _isoformat(row.get("huangyoucan_data_refreshed_at")),
        "huangyoucan_match_status": match_status,
    }


def resolve_huangyoucan_usage_for_identity(
    *,
    unionid: str,
    mobile: str,
    snapshots: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Pure resolver used by fixture paths and identity-contract tests."""

    items = [dict(item) for item in snapshots]
    union_candidates = [item for item in items if unionid and str(item.get("unionid") or "") == unionid]
    mobile_hash = normalized_mobile_md5(mobile)
    mobile_candidates = [item for item in items if mobile_hash and str(item.get("mobile_md5") or "") == mobile_hash]
    if len(union_candidates) == 1:
        union_candidate = union_candidates[0]
        if len(mobile_candidates) == 1 and str(mobile_candidates[0].get("huangyoucan_user_id")) != str(union_candidate.get("huangyoucan_user_id")):
            return public_huangyoucan_usage_fields({"huangyoucan_match_status": "ambiguous"})
        return public_huangyoucan_usage_fields(_snapshot_row(union_candidate, match_status="matched_unionid"))
    if len(union_candidates) > 1:
        return public_huangyoucan_usage_fields({"huangyoucan_match_status": "ambiguous"})
    if len(mobile_candidates) == 1:
        return public_huangyoucan_usage_fields(_snapshot_row(mobile_candidates[0], match_status="matched_mobile"))
    status = "ambiguous" if len(mobile_candidates) > 1 else "not_found"
    return public_huangyoucan_usage_fields({"huangyoucan_match_status": status})


def _snapshot_row(snapshot: dict[str, Any], *, match_status: str) -> dict[str, Any]:
    return {
        "huangyoucan_match_status": match_status,
        "huangyoucan_formally_logged_in": snapshot.get("formally_logged_in"),
        "huangyoucan_has_token_usage": snapshot.get("has_token_usage"),
        "huangyoucan_learning_plan_current": snapshot.get("learning_plan_current"),
        "huangyoucan_learning_plan_total": snapshot.get("learning_plan_total"),
        "huangyoucan_open_count_7d": snapshot.get("open_count_7d"),
        "huangyoucan_last_open_at": snapshot.get("last_open_at"),
        "huangyoucan_data_refreshed_at": snapshot.get("refreshed_at"),
    }


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _isoformat(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)
