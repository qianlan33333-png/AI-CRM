from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .hxc_projection_sql import build_hxc_scoped_projection_insert_sql


HXC_INCREMENTAL_DEFAULT_BATCH_SIZE = 5_000
HXC_INCREMENTAL_MAX_BATCH_SIZE = 5_000


@dataclass(frozen=True)
class HxcIncrementalSource:
    name: str
    cursor_kind: str
    sql: str
    optional_table: str = ""

    @property
    def initial_cursor_key(self) -> str:
        return "0" if self.cursor_kind == "bigint" else ""


@dataclass(frozen=True)
class HxcSourceScanResult:
    source_name: str
    scanned_count: int
    has_more: bool
    watermark: dict[str, Any]
    unionids: frozenset[str]
    mobile_hashes: frozenset[str]
    external_userids: frozenset[str]


_IDENTITY_SOURCE_SQL = """
SELECT identity.updated_at AS source_at,
       identity.unionid::text AS source_key,
       identity.unionid::text AS unionid,
       ''::text AS mobile_hash,
       ''::text AS external_userid
FROM public.crm_user_identity identity
WHERE COALESCE(identity.unionid, '') <> ''
  AND ROW(identity.updated_at, identity.unionid) > ROW(
      CAST(:cursor_at AS timestamptz), :cursor_key
  )
  AND identity.updated_at < CAST(:scan_before AS timestamptz)
ORDER BY identity.updated_at, identity.unionid
LIMIT :fetch_limit
"""


_DASHBOARD_SOURCE_SQL = """
SELECT snapshot.refreshed_at AS source_at,
       snapshot.id::text AS source_key,
       COALESCE(snapshot.unionid, '')::text AS unionid,
       CASE
           WHEN COALESCE(snapshot.unionid, '') = ''
            AND COALESCE(snapshot.mobile, '') <> '' THEN md5(snapshot.mobile)
           ELSE ''
       END::text AS mobile_hash,
       ''::text AS external_userid
FROM public.user_ops_hxc_dashboard_snapshot snapshot
WHERE ROW(snapshot.refreshed_at, snapshot.id) > ROW(
      CAST(:cursor_at AS timestamptz), CAST(:cursor_key AS bigint)
  )
  AND snapshot.refreshed_at < CAST(:scan_before AS timestamptz)
ORDER BY snapshot.refreshed_at, snapshot.id
LIMIT :fetch_limit
"""


_RECENT_MESSAGE_SOURCE_SQL = """
SELECT message.send_time AS source_at,
       message.id::text AS source_key,
       COALESCE(message.unionid, '')::text AS unionid,
       ''::text AS mobile_hash,
       ''::text AS external_userid
FROM public.customer_recent_message_next message
WHERE ROW(message.send_time, message.id) > ROW(
      CAST(:cursor_at AS timestamptz), CAST(:cursor_key AS bigint)
  )
  AND message.send_time < CAST(:scan_before AS timestamptz)
ORDER BY message.send_time, message.id
LIMIT :fetch_limit
"""


_AUTOMATION_JOB_SOURCE_SQL = """
SELECT COALESCE(job.updated_at, job.created_at) AS source_at,
       job.id::text AS source_key,
       ''::text AS unionid,
       CASE
           WHEN COALESCE(job.phone, '') <> '' THEN md5(job.phone)
           ELSE ''
       END::text AS mobile_hash,
       COALESCE(job.external_contact_id, '')::text AS external_userid
FROM public.automation_laohuang_chat_job job
WHERE ROW(COALESCE(job.updated_at, job.created_at), job.id) > ROW(
      CAST(:cursor_at AS timestamptz), CAST(:cursor_key AS bigint)
  )
  AND COALESCE(job.updated_at, job.created_at) < CAST(:scan_before AS timestamptz)
ORDER BY COALESCE(job.updated_at, job.created_at), job.id
LIMIT :fetch_limit
"""


_HXC_ACTIVATION_SOURCE_SQL = """
SELECT COALESCE(activation.updated_at, activation.created_at) AS source_at,
       activation.id::text AS source_key,
       ''::text AS unionid,
       CASE
           WHEN COALESCE(activation.mobile, '') <> '' THEN md5(activation.mobile)
           ELSE ''
       END::text AS mobile_hash,
       ''::text AS external_userid
FROM public.user_ops_huangxiaocan_activation_source activation
WHERE ROW(COALESCE(activation.updated_at, activation.created_at), activation.id) > ROW(
      CAST(:cursor_at AS timestamptz), CAST(:cursor_key AS bigint)
  )
  AND COALESCE(activation.updated_at, activation.created_at) < CAST(:scan_before AS timestamptz)
ORDER BY COALESCE(activation.updated_at, activation.created_at), activation.id
LIMIT :fetch_limit
"""


_ACTIVATION_STATUS_SOURCE_SQL = """
SELECT COALESCE(activation.updated_at, activation.created_at) AS source_at,
       activation.id::text AS source_key,
       ''::text AS unionid,
       CASE
           WHEN COALESCE(activation.mobile, '') <> '' THEN md5(activation.mobile)
           ELSE ''
       END::text AS mobile_hash,
       ''::text AS external_userid
FROM public.user_ops_activation_status_source activation
WHERE ROW(COALESCE(activation.updated_at, activation.created_at), activation.id) > ROW(
      CAST(:cursor_at AS timestamptz), CAST(:cursor_key AS bigint)
  )
  AND COALESCE(activation.updated_at, activation.created_at) < CAST(:scan_before AS timestamptz)
ORDER BY COALESCE(activation.updated_at, activation.created_at), activation.id
LIMIT :fetch_limit
"""


HXC_INCREMENTAL_SOURCES: tuple[HxcIncrementalSource, ...] = (
    HxcIncrementalSource("crm_user_identity", "text", _IDENTITY_SOURCE_SQL),
    HxcIncrementalSource(
        "user_ops_hxc_dashboard_snapshot",
        "bigint",
        _DASHBOARD_SOURCE_SQL,
    ),
    HxcIncrementalSource(
        "customer_recent_message_next",
        "bigint",
        _RECENT_MESSAGE_SOURCE_SQL,
    ),
    HxcIncrementalSource(
        "automation_laohuang_chat_job",
        "bigint",
        _AUTOMATION_JOB_SOURCE_SQL,
        optional_table="automation_laohuang_chat_job",
    ),
    HxcIncrementalSource(
        "user_ops_huangxiaocan_activation_source",
        "bigint",
        _HXC_ACTIVATION_SOURCE_SQL,
        optional_table="user_ops_huangxiaocan_activation_source",
    ),
    HxcIncrementalSource(
        "user_ops_activation_status_source",
        "bigint",
        _ACTIVATION_STATUS_SOURCE_SQL,
        optional_table="user_ops_activation_status_source",
    ),
)


def active_incremental_sources(optional_sources: set[str]) -> tuple[HxcIncrementalSource, ...]:
    return tuple(source for source in HXC_INCREMENTAL_SOURCES if not source.optional_table or source.optional_table in optional_sources)


def initial_source_watermarks(
    optional_sources: set[str],
    *,
    baseline_at: datetime,
    calibrated_at: datetime,
) -> dict[str, Any]:
    baseline_text = _public_datetime(baseline_at)
    calibrated_text = _public_datetime(calibrated_at)
    result: dict[str, Any] = {
        "full_calibration_at": calibrated_text,
        "legacy_registration_views": {
            "mode": "daily_only",
            "calibrated_at": calibrated_text,
        },
    }
    for source in active_incremental_sources(optional_sources):
        result[source.name] = {
            "mode": "incremental",
            "watermark_at": baseline_text,
            "cursor_key": source.initial_cursor_key,
            "has_more": False,
        }
    if "new_version_user_subscriptions" in optional_sources:
        result["new_version_user_subscriptions"] = {
            "mode": "daily_only",
            "calibrated_at": calibrated_text,
        }
    return result


def public_source_watermarks(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    public: dict[str, Any] = {}
    for source_name, source_state in payload.items():
        if source_name == "full_calibration_at":
            public[source_name] = str(source_state or "")
            continue
        if not isinstance(source_state, dict):
            continue
        public[source_name] = {key: source_state.get(key) for key in ("mode", "watermark_at", "calibrated_at", "has_more") if key in source_state}
    return public


def scan_source_changes(
    session: Session,
    source: HxcIncrementalSource,
    *,
    state: Any,
    fallback_at: datetime,
    scan_before: datetime,
    limit: int,
) -> HxcSourceScanResult:
    normalized_state = state if isinstance(state, dict) else {}
    cursor_at = _parse_datetime(normalized_state.get("watermark_at")) or fallback_at
    cursor_key = str(normalized_state.get("cursor_key") if normalized_state.get("cursor_key") is not None else source.initial_cursor_key)
    rows = (
        session.execute(
            text(source.sql),
            {
                "cursor_at": cursor_at,
                "cursor_key": cursor_key,
                "scan_before": scan_before,
                "fetch_limit": max(1, int(limit)) + 1,
            },
        )
        .mappings()
        .all()
    )
    selected = rows[: max(1, int(limit))]
    has_more = len(rows) > len(selected)

    unionids: set[str] = set()
    mobile_hashes: set[str] = set()
    external_userids: set[str] = set()
    for row in selected:
        _add_if_text(unionids, row.get("unionid"))
        _add_if_text(mobile_hashes, row.get("mobile_hash"))
        _add_if_text(external_userids, row.get("external_userid"))

    if has_more and selected:
        watermark_at = _as_datetime(selected[-1].get("source_at")) or cursor_at
        next_cursor_key = str(selected[-1].get("source_key") or source.initial_cursor_key)
    else:
        watermark_at = scan_before
        next_cursor_key = source.initial_cursor_key

    return HxcSourceScanResult(
        source_name=source.name,
        scanned_count=len(selected),
        has_more=has_more,
        watermark={
            "mode": "incremental",
            "watermark_at": _public_datetime(watermark_at),
            "cursor_key": next_cursor_key,
            "has_more": has_more,
        },
        unionids=frozenset(unionids),
        mobile_hashes=frozenset(mobile_hashes),
        external_userids=frozenset(external_userids),
    )


def resolve_dirty_unionids(
    session: Session,
    *,
    generation: int,
    unionids: set[str],
    mobile_hashes: set[str],
    external_userids: set[str],
) -> set[str]:
    if not unionids and not mobile_hashes and not external_userids:
        return set()
    rows = session.execute(
        text(
            """
            WITH union_keys AS MATERIALIZED (
                SELECT value
                FROM jsonb_array_elements_text(
                    CAST(:unionids_json AS jsonb)
                ) AS item(value)
            ),
            mobile_keys AS MATERIALIZED (
                SELECT value
                FROM jsonb_array_elements_text(
                    CAST(:mobile_hashes_json AS jsonb)
                ) AS item(value)
            ),
            external_keys AS MATERIALIZED (
                SELECT value
                FROM jsonb_array_elements_text(
                    CAST(:external_userids_json AS jsonb)
                ) AS item(value)
            )
            SELECT DISTINCT resolved.unionid
            FROM (
                SELECT value AS unionid
                FROM union_keys

                UNION ALL

                SELECT contact.unionid
                FROM audience_read.wecom_contacts_v1 contact
                JOIN mobile_keys key ON key.value = contact.mobile_hash

                UNION ALL

                SELECT contact.unionid
                FROM audience_read.wecom_contacts_v1 contact
                JOIN external_keys key ON key.value = contact.external_userid

                UNION ALL

                SELECT projection.unionid
                FROM ai_audience_hxc_member_usage_projection projection
                JOIN mobile_keys key ON key.value = projection.mobile_hash
                WHERE projection.generation = :generation
            ) resolved
            WHERE COALESCE(resolved.unionid, '') <> ''
            """
        ),
        {
            "generation": int(generation),
            "unionids_json": _json_array(unionids),
            "mobile_hashes_json": _json_array(mobile_hashes),
            "external_userids_json": _json_array(external_userids),
        },
    ).scalars()
    return {str(value).strip() for value in rows if str(value or "").strip()}


def replace_dirty_projection_rows(
    session: Session,
    *,
    generation: int,
    dirty_unionids: set[str],
    optional_sources: set[str],
) -> tuple[int, int]:
    if not dirty_unionids:
        return 0, 0
    dirty_unionids_json = _json_array(dirty_unionids)
    deleted = session.execute(
        text(
            """
            DELETE FROM ai_audience_hxc_member_usage_projection
            WHERE generation = :generation
              AND unionid IN (
                  SELECT value
                  FROM jsonb_array_elements_text(
                      CAST(:dirty_unionids_json AS jsonb)
                  ) AS dirty(value)
              )
            """
        ),
        {
            "generation": int(generation),
            "dirty_unionids_json": dirty_unionids_json,
        },
    )
    inserted = session.execute(
        text(build_hxc_scoped_projection_insert_sql(optional_sources)),
        {
            "generation": int(generation),
            "dirty_unionids_json": dirty_unionids_json,
        },
    )
    return max(0, int(deleted.rowcount or 0)), max(0, int(inserted.rowcount or 0))


def oldest_incremental_watermark(
    source_watermarks: dict[str, Any],
    *,
    fallback: datetime,
) -> datetime:
    values = [
        parsed
        for state in source_watermarks.values()
        if isinstance(state, dict) and state.get("mode") == "incremental"
        for parsed in [_parse_datetime(state.get("watermark_at"))]
        if parsed is not None
    ]
    return min(values) if values else fallback


def _json_array(values: set[str]) -> str:
    return json.dumps(sorted(values), ensure_ascii=False, separators=(",", ":"))


def _add_if_text(target: set[str], value: Any) -> None:
    normalized = str(value or "").strip()
    if normalized:
        target.add(normalized)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_datetime(value)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _as_datetime(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _public_datetime(value: datetime) -> str:
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
