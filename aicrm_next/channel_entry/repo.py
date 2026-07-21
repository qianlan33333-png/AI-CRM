from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
from typing import Any
from uuid import UUID

from aicrm_next.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.identity_contact.resolver import resolve_external_userid_with_dbapi, resolve_identity_with_dbapi, resolved_unionid
from aicrm_next.shared.runtime import raw_database_url

from .domain import text


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def _connect():
    url = _psycopg_url(raw_database_url())
    if not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL is required for channel_entry production repository")
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(url, row_factory=dict_row)


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
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
    return json.dumps(json_safe(value if value is not None else {}), ensure_ascii=False)


def _json(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(json_safe(value if value is not None else {}), dumps=_json_dumps)


def _with_effect_log_diagnostic_identity(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("request_json") if isinstance(row.get("request_json"), dict) else {}
    row.setdefault("external_contact_id", text(payload.get("external_contact_id")))
    return row


def find_channel_by_scene_value(scene_value: str) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM automation_channel
            WHERE scene_value = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (text(scene_value),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def find_channel_by_scene_alias(corp_id: str, scene_value: str) -> dict[str, Any] | None:
    sql = """
        SELECT c.*,
               a.id AS scene_alias_id,
               a.corp_id AS scene_alias_corp_id,
               a.scene_value AS scene_alias_value,
               a.status AS scene_alias_status,
               a.source AS scene_alias_source
        FROM automation_channel_scene_alias a
        JOIN automation_channel c ON c.id = a.channel_id
        WHERE a.corp_id = %s
          AND a.scene_value = %s
          AND a.status <> 'revoked'
        ORDER BY CASE WHEN a.status = 'active' THEN 0 ELSE 1 END, a.updated_at DESC, a.id DESC
        LIMIT 1
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (text(corp_id), text(scene_value)))
        row = cur.fetchone()
        if row:
            return dict(row)
        cur.execute(sql, ("", text(scene_value)))
        row = cur.fetchone()
        return dict(row) if row else None


def find_confirmed_channel_by_scene_alias(corp_id: str, scene_value: str) -> dict[str, Any] | None:
    allowed_sources = ("next_create_contact_way", "legacy_import_confirmed", "admin_repair_confirmed")
    sql = """
        SELECT c.*,
               a.id AS scene_alias_id,
               a.corp_id AS scene_alias_corp_id,
               a.scene_value AS scene_alias_value,
               a.status AS scene_alias_status,
               a.source AS scene_alias_source
        FROM automation_channel_scene_alias a
        JOIN automation_channel c ON c.id = a.channel_id
        WHERE a.corp_id = %s
          AND a.scene_value = %s
          AND a.status IN ('active', 'retired')
          AND a.source = ANY(%s)
        ORDER BY CASE WHEN a.status = 'active' THEN 0 ELSE 1 END, a.updated_at DESC, a.id DESC
        LIMIT 1
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (text(corp_id), text(scene_value), list(allowed_sources)))
        row = cur.fetchone()
        if row:
            return dict(row)
        cur.execute(sql, ("", text(scene_value), list(allowed_sources)))
        row = cur.fetchone()
        return dict(row) if row else None


def find_channel_by_historical_scene_value(scene_value: str) -> dict[str, Any] | None:
    # Channel entry is now channel-only; unknown scenes should remain diagnostic
    # instead of being inferred from retired program-admission projections.
    return None


def qrcode_asset_hash(qr_url: str) -> str:
    value = text(qr_url)
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def get_active_qrcode_asset(channel_id: int) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM automation_channel_qrcode_asset
            WHERE channel_id = %s
              AND status = 'active'
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            """,
            (int(channel_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def resolve_external_contact_customer_name(external_userid: str, *, corp_id: str = "") -> str:
    external = text(external_userid)
    if not external:
        return ""
    with _connect() as conn, conn.cursor() as cur:
        resolution = resolve_identity_with_dbapi(
            cur,
            ResolvePersonIdentityRequest(external_userid=external),
        )
    identity = resolution.identity if resolution.status == "resolved" else None
    return text((identity.customer_name or identity.remark) if identity else "")


def list_channel_qrcode_assets(channel_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM automation_channel_qrcode_asset
            WHERE channel_id = %s
            ORDER BY generated_at DESC, id DESC
            LIMIT %s
            """,
            (int(channel_id), max(1, min(int(limit or 20), 100))),
        )
        return [dict(row) for row in cur.fetchall() or []]


def find_qrcode_asset_by_scene(corp_id: str, scene_value: str) -> dict[str, Any] | None:
    sql = """
        SELECT a.*,
               c.id AS channel_row_id,
               c.channel_code,
               c.channel_name,
               c.channel_type,
               c.carrier_type,
               c.scene_value AS channel_scene_value,
               c.qr_url AS channel_qr_url,
               c.status AS channel_status,
               c.owner_staff_id,
               c.welcome_message,
               c.welcome_image_library_ids,
               c.welcome_miniprogram_library_ids,
               c.welcome_attachment_library_ids,
               c.welcome_group_invite_library_ids,
               c.entry_tag_id,
               c.entry_tag_name,
               c.entry_tag_group_name
        FROM automation_channel_qrcode_asset a
        JOIN automation_channel c ON c.id = a.channel_id
        WHERE a.corp_id = %s
          AND a.scene_value = %s
        ORDER BY CASE a.status WHEN 'active' THEN 0 WHEN 'retired' THEN 1 ELSE 2 END,
                 a.generated_at DESC,
                 a.id DESC
        LIMIT 1
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (text(corp_id), text(scene_value)))
        row = cur.fetchone()
        if row:
            return dict(row)
        cur.execute(sql, ("", text(scene_value)))
        row = cur.fetchone()
        return dict(row) if row else None


def retire_active_qrcode_assets(channel_id: int, *, except_asset_id: int | None = None) -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE automation_channel_qrcode_asset
            SET status = 'retired',
                retired_at = COALESCE(retired_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE channel_id = %s
              AND status = 'active'
              AND (%s IS NULL OR id <> %s)
            """,
            (int(channel_id), except_asset_id, except_asset_id),
        )
        count = int(cur.rowcount or 0)
        conn.commit()
        return count


def mark_qrcode_asset_stale(channel_id: int, *, reason: str = "channel_owner_changed") -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE automation_channel_qrcode_asset
            SET status = 'stale',
                provider_payload_json = provider_payload_json || %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE channel_id = %s
              AND status = 'active'
            """,
            (_json({"stale_reason": text(reason)}), int(channel_id)),
        )
        count = int(cur.rowcount or 0)
        conn.commit()
        return count


def insert_qrcode_asset(
    *,
    channel_id: int,
    scene_value: str,
    config_id: str,
    qr_url: str,
    corp_id: str = "",
    provider_name: str = "wecom_contact_way",
    provider_payload_json: dict[str, Any] | None = None,
    status: str = "active",
    generation_source: str = "",
    created_by: str = "",
) -> dict[str, Any]:
    normalized_status = text(status) or "active"
    if normalized_status not in {"active", "retired", "revoked", "stale", "quarantined"}:
        normalized_status = "active"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO automation_channel_qrcode_asset (
                corp_id, channel_id, scene_value, config_id, qr_url, qr_url_hash,
                provider_name, provider_payload_json, status, generation_source,
                created_by, generated_at, retired_at, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP,
                    CASE WHEN %s = 'retired' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (corp_id, scene_value) DO UPDATE
            SET config_id = CASE WHEN EXCLUDED.config_id <> '' THEN EXCLUDED.config_id ELSE automation_channel_qrcode_asset.config_id END,
                qr_url = CASE WHEN EXCLUDED.qr_url <> '' THEN EXCLUDED.qr_url ELSE automation_channel_qrcode_asset.qr_url END,
                qr_url_hash = CASE WHEN EXCLUDED.qr_url_hash <> '' THEN EXCLUDED.qr_url_hash ELSE automation_channel_qrcode_asset.qr_url_hash END,
                provider_name = EXCLUDED.provider_name,
                provider_payload_json = EXCLUDED.provider_payload_json,
                status = EXCLUDED.status,
                generation_source = CASE WHEN EXCLUDED.generation_source <> '' THEN EXCLUDED.generation_source ELSE automation_channel_qrcode_asset.generation_source END,
                created_by = CASE WHEN EXCLUDED.created_by <> '' THEN EXCLUDED.created_by ELSE automation_channel_qrcode_asset.created_by END,
                retired_at = CASE WHEN EXCLUDED.status = 'retired' AND automation_channel_qrcode_asset.retired_at IS NULL THEN CURRENT_TIMESTAMP WHEN EXCLUDED.status <> 'retired' THEN NULL ELSE automation_channel_qrcode_asset.retired_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE automation_channel_qrcode_asset.channel_id = EXCLUDED.channel_id
            RETURNING *
            """,
            (
                text(corp_id),
                int(channel_id),
                text(scene_value),
                text(config_id),
                text(qr_url),
                qrcode_asset_hash(qr_url),
                text(provider_name) or "wecom_contact_way",
                _json(provider_payload_json or {}),
                normalized_status,
                text(generation_source),
                text(created_by),
                normalized_status,
            ),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                """
                SELECT *, TRUE AS conflict, 'qrcode_asset_scene_channel_conflict' AS reason
                FROM automation_channel_qrcode_asset
                WHERE corp_id = %s AND scene_value = %s
                LIMIT 1
                """,
                (text(corp_id), text(scene_value)),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}


def touch_qrcode_asset_callback(asset_id: int) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE automation_channel_qrcode_asset
            SET last_callback_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (int(asset_id),),
        )
        conn.commit()


def upsert_channel_scene_alias(
    *,
    channel_id: int,
    scene_value: str,
    corp_id: str = "",
    config_id: str = "",
    qr_url: str = "",
    carrier_type: str = "qrcode",
    provider_name: str = "wecom_contact_way",
    status: str = "active",
    source: str = "",
) -> dict[str, Any]:
    normalized_status = text(status) or "active"
    if normalized_status not in {"active", "retired", "revoked"}:
        normalized_status = "active"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM automation_channel_scene_alias WHERE corp_id = %s AND scene_value = %s FOR UPDATE", (text(corp_id), text(scene_value)))
        existing = cur.fetchone()
        if existing and int(existing.get("channel_id") or 0) != int(channel_id):
            return {
                **dict(existing),
                "conflict": True,
                "reason": "scene_alias_channel_conflict",
                "attempted_channel_id": int(channel_id),
            }
        if existing:
            cur.execute(
                """
                UPDATE automation_channel_scene_alias
                SET config_id = CASE WHEN %s <> '' THEN %s ELSE config_id END,
                    qr_url = CASE WHEN %s <> '' THEN %s ELSE qr_url END,
                    carrier_type = %s,
                    provider_name = %s,
                    status = CASE WHEN status = 'revoked' THEN status ELSE %s END,
                    source = CASE WHEN %s <> '' THEN %s ELSE source END,
                    last_seen_at = CURRENT_TIMESTAMP,
                    retired_at = CASE WHEN %s = 'retired' AND retired_at IS NULL THEN CURRENT_TIMESTAMP WHEN %s <> 'retired' THEN NULL ELSE retired_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """,
                (
                    text(config_id),
                    text(config_id),
                    text(qr_url),
                    text(qr_url),
                    text(carrier_type) or "qrcode",
                    text(provider_name) or "wecom_contact_way",
                    normalized_status,
                    text(source),
                    text(source),
                    normalized_status,
                    normalized_status,
                    int(existing["id"]),
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO automation_channel_scene_alias (
                    corp_id, channel_id, scene_value, config_id, qr_url, carrier_type,
                    provider_name, status, source, first_seen_at, last_seen_at,
                    retired_at, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                    CASE WHEN %s = 'retired' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING *
                """,
                (
                    text(corp_id),
                    int(channel_id),
                    text(scene_value),
                    text(config_id),
                    text(qr_url),
                    text(carrier_type) or "qrcode",
                    text(provider_name) or "wecom_contact_way",
                    normalized_status,
                    text(source),
                    normalized_status,
                ),
            )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}


def retire_channel_scene_alias(channel_id: int, scene_value: str) -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE automation_channel_scene_alias
            SET status = 'retired', retired_at = COALESCE(retired_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
            WHERE channel_id = %s AND scene_value = %s AND status = 'active'
            """,
            (int(channel_id), text(scene_value)),
        )
        count = int(cur.rowcount or 0)
        conn.commit()
        return count


def revoke_channel_scene_alias(channel_id: int, scene_value: str) -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE automation_channel_scene_alias
            SET status = 'revoked', updated_at = CURRENT_TIMESTAMP
            WHERE channel_id = %s AND scene_value = %s
            """,
            (int(channel_id), text(scene_value)),
        )
        count = int(cur.rowcount or 0)
        conn.commit()
        return count


def list_channel_scene_aliases(channel_id: int) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM automation_channel_scene_alias
            WHERE channel_id = %s
            ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'retired' THEN 1 ELSE 2 END, updated_at DESC, id DESC
            """,
            (int(channel_id),),
        )
        return [dict(row) for row in cur.fetchall() or []]


def backfill_scene_alias_from_historical_vote(scene_value: str, channel_id: int) -> dict[str, Any]:
    channel = get_channel_by_id(int(channel_id)) or {}
    return upsert_channel_scene_alias(
        channel_id=int(channel_id),
        scene_value=text(scene_value),
        qr_url=text(channel.get("qr_url")),
        carrier_type=text(channel.get("carrier_type")) or "qrcode",
        status="active",
        source="historical_backfill",
    )


def update_alias_last_seen_at(corp_id: str, scene_value: str) -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE automation_channel_scene_alias
            SET last_seen_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE corp_id = %s AND scene_value = %s
            """,
            (text(corp_id), text(scene_value)),
        )
        count = int(cur.rowcount or 0)
        conn.commit()
        return count


def get_channel_by_id(channel_id: int) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM automation_channel WHERE id = %s LIMIT 1", (int(channel_id),))
        row = cur.fetchone()
        return dict(row) if row else None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_channel_assignees(
    assignees: list[dict[str, Any]] | None,
    *,
    strategy: str,
) -> list[dict[str, Any]]:
    normalized_strategy = text(strategy) or "ratio"
    if normalized_strategy not in {"ratio", "cap_switch"}:
        raise ValueError("invalid_assignment_strategy")
    source = assignees or []
    if not isinstance(source, list):
        raise ValueError("assignees_must_be_array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(source, start=1):
        item = dict(raw or {})
        staff_id = text(item.get("staff_id"))
        if not staff_id:
            raise ValueError("staff_id_required")
        if staff_id in seen:
            raise ValueError("staff_id_duplicated")
        seen.add(staff_id)
        status = text(item.get("status")) or "active"
        if status not in {"active", "inactive", "archived"}:
            status = "active"
        priority = _int_or_none(item.get("priority")) or index
        ratio_percent = _int_or_none(item.get("ratio_percent"))
        max_scans_24h = _int_or_none(item.get("max_scans_24h"))
        if normalized_strategy == "ratio":
            max_scans_24h = None
        if normalized_strategy == "cap_switch":
            ratio_percent = None
        result.append(
            {
                "staff_id": staff_id,
                "display_name": text(item.get("display_name") or item.get("display_name_snapshot") or staff_id),
                "display_name_snapshot": text(item.get("display_name_snapshot") or item.get("display_name") or staff_id),
                "priority": int(priority),
                "ratio_percent": ratio_percent,
                "max_scans_24h": max_scans_24h,
                "status": status,
            }
        )
    active = [item for item in result if item["status"] == "active"]
    if not (1 <= len(active) <= 5):
        raise ValueError("active_assignees_count_must_be_1_to_5")
    if normalized_strategy == "ratio":
        total = 0
        for item in active:
            ratio = item.get("ratio_percent")
            if ratio is None or int(ratio) <= 0:
                raise ValueError("ratio_percent_must_be_positive")
            total += int(ratio)
        if total != 100:
            raise ValueError("ratio_percent_total_must_equal_100")
    if normalized_strategy == "cap_switch":
        for item in active:
            cap = item.get("max_scans_24h")
            if cap is None or int(cap) <= 0:
                raise ValueError("max_scans_24h_must_be_positive")
    return result


def _serialize_assignee(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "channel_id": int(row.get("channel_id") or 0),
        "staff_id": text(row.get("staff_id")),
        "display_name": text(row.get("display_name_snapshot") or row.get("display_name") or row.get("staff_id")),
        "display_name_snapshot": text(row.get("display_name_snapshot") or row.get("display_name") or row.get("staff_id")),
        "priority": int(row.get("priority") or 0),
        "ratio_percent": _int_or_none(row.get("ratio_percent")),
        "max_scans_24h": _int_or_none(row.get("max_scans_24h")),
        "status": text(row.get("status")) or "active",
    }


def list_channel_assignees(channel_id: int, *, active_only: bool = False) -> list[dict[str, Any]]:
    where_status = "AND status = 'active'" if active_only else ""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT *
            FROM automation_channel_assignee
            WHERE channel_id = %s
              {where_status}
            ORDER BY priority ASC, id ASC
            """,
            (int(channel_id),),
        )
        return [_serialize_assignee(dict(row)) for row in cur.fetchall() or []]


def save_channel_assignees(
    channel_id: int,
    *,
    assignment_mode: str,
    assignment_strategy: str,
    assignees: list[dict[str, Any]] | None,
    overflow_policy: str = "",
) -> dict[str, Any]:
    channel = get_channel_by_id(int(channel_id))
    if not channel:
        raise LookupError("channel_not_found")
    normalized_mode = text(assignment_mode) or "multi_staff"
    if normalized_mode not in {"single_owner", "multi_staff"}:
        raise ValueError("invalid_assignment_mode")
    normalized_strategy = text(assignment_strategy) or "ratio"
    normalized = normalize_channel_assignees(assignees or [], strategy=normalized_strategy)
    normalized_overflow = text(overflow_policy) or "least_loaded"
    active_staff_ids = [item["staff_id"] for item in normalized if item["status"] == "active"]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE automation_channel
            SET assignment_mode = %s,
                assignment_strategy = %s,
                overflow_policy = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (normalized_mode, normalized_strategy, normalized_overflow, int(channel_id)),
        )
        for item in normalized:
            cur.execute(
                """
                INSERT INTO automation_channel_assignee (
                    channel_id, staff_id, display_name_snapshot, priority,
                    ratio_percent, max_scans_24h, status, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (channel_id, staff_id) DO UPDATE
                SET display_name_snapshot = EXCLUDED.display_name_snapshot,
                    priority = EXCLUDED.priority,
                    ratio_percent = EXCLUDED.ratio_percent,
                    max_scans_24h = EXCLUDED.max_scans_24h,
                    status = EXCLUDED.status,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(channel_id),
                    item["staff_id"],
                    item["display_name_snapshot"],
                    int(item["priority"]),
                    item.get("ratio_percent"),
                    item.get("max_scans_24h"),
                    item["status"],
                ),
            )
        cur.execute(
            """
            UPDATE automation_channel_assignee
            SET status = 'archived',
                updated_at = CURRENT_TIMESTAMP
            WHERE channel_id = %s
              AND status = 'active'
              AND NOT (staff_id = ANY(%s))
            """,
            (int(channel_id), active_staff_ids),
        )
        conn.commit()
    return {
        "channel_id": int(channel_id),
        "assignment_mode": normalized_mode,
        "assignment_strategy": normalized_strategy,
        "overflow_policy": normalized_overflow,
        "assignees": list_channel_assignees(int(channel_id)),
    }


def list_assignment_stats_24h(channel_id: int) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT assignee_staff_id, COUNT(*)::int AS assigned_count
            FROM automation_channel_assignment_event
            WHERE channel_id = %s
              AND status = 'assigned'
              AND assigned_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
            GROUP BY assignee_staff_id
            """,
            (int(channel_id),),
        )
        counts = {text(row.get("assignee_staff_id")): int(row.get("assigned_count") or 0) for row in cur.fetchall() or []}
    stats: list[dict[str, Any]] = []
    for item in list_channel_assignees(int(channel_id), active_only=True):
        stats.append(
            {
                "staff_id": item["staff_id"],
                "assignee_staff_id": item["staff_id"],
                "assigned_count": int(counts.get(item["staff_id"], 0)),
                "window": "24h",
            }
        )
    return stats


def _serialize_assignment_event(row: dict[str, Any]) -> dict[str, Any]:
    source_payload = row.get("source_payload_json") if isinstance(row.get("source_payload_json"), dict) else {}
    return {
        "id": int(row.get("id") or 0),
        "channel_id": int(row.get("channel_id") or 0),
        "assignee_staff_id": text(row.get("assignee_staff_id")),
        "strategy": text(row.get("strategy")),
        "reason": text(row.get("reason")),
        "status": text(row.get("status")) or "assigned",
        "unionid": text(row.get("unionid")),
        "external_contact_id": text(source_payload.get("external_contact_id")),
        "wecom_user_id": text(row.get("wecom_user_id")),
        "assigned_at": row.get("assigned_at").isoformat() if isinstance(row.get("assigned_at"), datetime) else text(row.get("assigned_at")),
    }


def insert_assignment_event(
    *,
    channel_id: int,
    assignee_staff_id: str,
    strategy: str,
    reason: str,
    unionid: str = "",
    external_contact_id: str = "",
    wecom_user_id: str = "",
    source_payload_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        resolution = resolve_identity_with_dbapi(
            cur,
            ResolvePersonIdentityRequest(
                unionid=text(unionid) or None,
                external_userid=text(external_contact_id) or None,
            ),
        )
        canonical_unionid = resolved_unionid(resolution)
        if not canonical_unionid:
            raise ValueError("identity_pending_unionid")
        source_payload = dict(source_payload_json or {})
        if external_contact_id:
            source_payload.setdefault("external_contact_id", text(external_contact_id))
        cur.execute(
            """
            INSERT INTO automation_channel_assignment_event (
                channel_id, assignee_staff_id, strategy, reason, status,
                unionid, wecom_user_id, source_payload_json,
                assigned_at, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, 'assigned', %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING *
            """,
            (
                int(channel_id),
                text(assignee_staff_id),
                text(strategy),
                text(reason),
                canonical_unionid,
                text(wecom_user_id),
                _json(source_payload),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _serialize_assignment_event(dict(row)) if row else {}


def list_assignment_events(channel_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 50), 200))
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM automation_channel_assignment_event
            WHERE channel_id = %s
            ORDER BY assigned_at DESC, id DESC
            LIMIT %s
            """,
            (int(channel_id), safe_limit),
        )
        return [_serialize_assignment_event(dict(row)) for row in cur.fetchall() or []]


def _assignment_counts(channel_id: int, staff_ids: list[str], *, window_24h: bool = False) -> dict[str, int]:
    if not staff_ids:
        return {}
    window_clause = "AND assigned_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'" if window_24h else ""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT assignee_staff_id, COUNT(*)::int AS assigned_count
            FROM automation_channel_assignment_event
            WHERE channel_id = %s
              AND status = 'assigned'
              AND assignee_staff_id = ANY(%s)
              {window_clause}
            GROUP BY assignee_staff_id
            """,
            (int(channel_id), staff_ids),
        )
        return {text(row.get("assignee_staff_id")): int(row.get("assigned_count") or 0) for row in cur.fetchall() or []}


def choose_channel_assignee(
    channel_id: int,
    *,
    external_contact_id: str = "",
    wecom_user_id: str = "",
    write_event: bool = False,
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    channel = get_channel_by_id(int(channel_id))
    if not channel:
        raise LookupError("channel_not_found")
    strategy = text(channel.get("assignment_strategy")) or "ratio"
    assignees = list_channel_assignees(int(channel_id), active_only=True)
    normalize_channel_assignees(assignees, strategy=strategy)
    staff_ids = [item["staff_id"] for item in assignees]
    selected: dict[str, Any] | None = None
    reason = ""
    if strategy == "ratio":
        counts = _assignment_counts(int(channel_id), staff_ids)
        total_assigned = sum(int(counts.get(staff_id, 0)) for staff_id in staff_ids)
        ranked = []
        for item in assignees:
            ratio = int(item.get("ratio_percent") or 0)
            expected = (total_assigned + 1) * ratio / 100
            actual = int(counts.get(item["staff_id"], 0))
            ranked.append((expected - actual, int(item.get("priority") or 0), int(item.get("id") or 0), item))
        ranked.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
        selected = ranked[0][3] if ranked else None
        reason = "ratio_deficit_selected"
    elif strategy == "cap_switch":
        counts = _assignment_counts(int(channel_id), staff_ids, window_24h=True)
        for item in assignees:
            if int(counts.get(item["staff_id"], 0)) < int(item.get("max_scans_24h") or 0):
                selected = item
                reason = "cap_switch_priority_available"
                break
        if selected is None:
            return {
                "ok": False,
                "channel_id": int(channel_id),
                "assignment_strategy": strategy,
                "reason": "all_assignees_reached_24h_cap",
                "source": "ai_crm_next",
            }
    else:
        raise ValueError("invalid_assignment_strategy")
    if selected is None:
        raise ValueError("active_assignees_required")
    event = None
    if write_event:
        event = insert_assignment_event(
            channel_id=int(channel_id),
            assignee_staff_id=selected["staff_id"],
            strategy=strategy,
            reason=reason,
            unionid=text((source_payload or {}).get("unionid")),
            external_contact_id=external_contact_id,
            wecom_user_id=wecom_user_id,
            source_payload_json=source_payload or {},
        )
    return {
        "ok": True,
        "channel_id": int(channel_id),
        "assignment_strategy": strategy,
        "assignee_staff_id": selected["staff_id"],
        "assignee": selected,
        "reason": reason,
        "event": event,
        "source": "ai_crm_next",
    }


def update_channel_qrcode(*, channel_id: int, scene_value: str, qr_url: str, config_id: str = "") -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE automation_channel
            SET scene_value = %s,
                qr_url = %s,
                qr_ticket = %s,
                carrier_type = CASE WHEN carrier_type = '' THEN 'qrcode' ELSE carrier_type END,
                channel_type = CASE WHEN channel_type = '' THEN 'qrcode' ELSE channel_type END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
            """,
            (text(scene_value), text(qr_url), text(config_id), int(channel_id)),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}


def upsert_channel_contact(*, channel_id: int, unionid: str = "", external_contact_id: str, owner_staff_id: str, source_payload: dict[str, Any]) -> dict[str, Any]:
    sanitized_payload = {
        key: value
        for key, value in dict(source_payload or {}).items()
        if key not in {"external_contact_id", "ExternalUserID", "external_userid"}
    }
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO automation_channel_contact (
                channel_id, unionid, owner_staff_id, source_payload_json,
                first_channel_entered_at, last_channel_entered_at, enter_count, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (channel_id, unionid) WHERE unionid <> ''
            DO UPDATE SET owner_staff_id = EXCLUDED.owner_staff_id,
                source_payload_json = EXCLUDED.source_payload_json,
                last_channel_entered_at = CURRENT_TIMESTAMP,
                enter_count = automation_channel_contact.enter_count + 1,
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
            """,
            (int(channel_id), text(unionid), text(owner_staff_id), _json(sanitized_payload)),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}


def upsert_channel_entry_runtime(
    *,
    corp_id: str,
    event_log_id: int | None = None,
    channel_id: int | None = None,
    scene_value: str = "",
    external_userid: str = "",
    follow_user_userid: str = "",
    welcome_code_present: bool = False,
    unionid: str = "",
    identity_status: str = "pending",
    runtime_status: str = "received",
    payload_json: dict[str, Any] | None = None,
    enqueue_identity_resolution: bool = False,
    identity_resolution_reason: str = "identity_pending_unionid",
    parent_execution_id: str = "",
) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO automation_channel_entry_runtime (
                corp_id, event_log_id, channel_id, scene_value, external_userid, follow_user_userid,
                welcome_code_present, unionid, identity_status, runtime_status, payload_json,
                first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                NOW(), NOW(), NOW(), NOW()
            )
            ON CONFLICT (corp_id, external_userid, follow_user_userid, scene_value)
            WHERE external_userid <> '' AND scene_value <> ''
            DO UPDATE SET
                event_log_id = COALESCE(EXCLUDED.event_log_id, automation_channel_entry_runtime.event_log_id),
                channel_id = COALESCE(EXCLUDED.channel_id, automation_channel_entry_runtime.channel_id),
                welcome_code_present = automation_channel_entry_runtime.welcome_code_present OR EXCLUDED.welcome_code_present,
                unionid = CASE
                    WHEN EXCLUDED.unionid <> '' THEN EXCLUDED.unionid
                    ELSE automation_channel_entry_runtime.unionid
                END,
                identity_status = EXCLUDED.identity_status,
                runtime_status = EXCLUDED.runtime_status,
                payload_json = automation_channel_entry_runtime.payload_json || EXCLUDED.payload_json,
                last_seen_at = NOW(),
                updated_at = NOW()
            RETURNING *
            """,
            (
                text(corp_id),
                event_log_id,
                channel_id,
                text(scene_value),
                text(external_userid),
                text(follow_user_userid),
                bool(welcome_code_present),
                text(unionid),
                text(identity_status) or "pending",
                text(runtime_status) or "received",
                _json(payload_json or {}),
            ),
        )
        row = cur.fetchone()
        identity_queue: dict[str, Any] = {}
        if row and enqueue_identity_resolution:
            from aicrm_next.identity_contact.resolution_effects import (
                enqueue_channel_entry_identity_resolution_in_connection,
            )

            identity_queue = enqueue_channel_entry_identity_resolution_in_connection(
                conn,
                corp_id=corp_id,
                external_userid=external_userid,
                follow_user_userid=follow_user_userid,
                payload_json=payload_json,
                reason=identity_resolution_reason,
                parent_execution_id=parent_execution_id,
                event_log_id=event_log_id,
            )
            if identity_queue.get("external_effect_job_id"):
                linked = conn.execute(
                    """
                    UPDATE automation_channel_entry_runtime
                    SET identity_external_effect_job_id = %s,
                        identity_execution_id = %s,
                        identity_parent_execution_id = %s,
                        identity_hold_reason = '',
                        identity_held_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        int(identity_queue["external_effect_job_id"]),
                        text(identity_queue.get("execution_id")),
                        text(identity_queue.get("parent_execution_id")),
                        int(row["id"]),
                    ),
                ).fetchone()
                row = linked or row
        conn.commit()
        result = dict(row) if row else {}
        if enqueue_identity_resolution:
            result["identity_resolution_queue"] = identity_queue
        return result


def mark_channel_entry_runtime_identity(
    *,
    event_log_id: int | None = None,
    corp_id: str = "",
    scene_value: str = "",
    external_userid: str = "",
    follow_user_userid: str = "",
    unionid: str = "",
    identity_status: str = "",
    identity_sync: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if event_log_id:
        clauses.append("event_log_id = %s")
        params.append(int(event_log_id))
    if text(external_userid) and text(scene_value):
        clauses.append(
            "(corp_id = %s AND external_userid = %s AND follow_user_userid = %s AND scene_value = %s)"
        )
        params.extend([text(corp_id), text(external_userid), text(follow_user_userid), text(scene_value)])
    if not clauses:
        return {"status": "skipped", "reason": "runtime_lookup_missing", "updated_count": 0}
    payload = {"identity_sync": json_safe(identity_sync or {})}
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE automation_channel_entry_runtime
            SET unionid = CASE WHEN %s <> '' THEN %s ELSE unionid END,
                identity_status = %s,
                payload_json = payload_json || %s,
                last_seen_at = NOW(),
                updated_at = NOW()
            WHERE {" OR ".join(clauses)}
            """,
            (
                text(unionid),
                text(unionid),
                text(identity_status) or "pending",
                _json(payload),
                *params,
            ),
        )
        updated_count = int(cur.rowcount or 0)
        conn.commit()
    return {"status": "success", "updated_count": updated_count}


def enqueue_channel_entry_identity_resolution(
    *,
    corp_id: str,
    external_userid: str,
    follow_user_userid: str = "",
    payload_json: dict[str, Any] | None = None,
    reason: str = "identity_pending_unionid",
    parent_execution_id: str = "",
    event_log_id: int | None = None,
) -> dict[str, Any]:
    from aicrm_next.identity_contact.resolution_effects import (
        enqueue_channel_entry_identity_resolution_in_connection,
    )

    with _connect() as conn:
        planned = enqueue_channel_entry_identity_resolution_in_connection(
            conn,
            corp_id=corp_id,
            external_userid=external_userid,
            follow_user_userid=follow_user_userid,
            payload_json=payload_json,
            reason=reason,
            parent_execution_id=parent_execution_id,
            event_log_id=event_log_id,
        )
        conn.commit()
    return planned


def get_channel_entry_effect_log(effect_type: str, idempotency_key: str) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM automation_channel_entry_effect_log WHERE effect_type = %s AND idempotency_key = %s LIMIT 1",
            (text(effect_type), text(idempotency_key)),
        )
        row = cur.fetchone()
        return _with_effect_log_diagnostic_identity(dict(row)) if row else None


def upsert_channel_entry_effect_log(
    *,
    effect_type: str,
    idempotency_key: str,
    status: str,
    event_log_id: int | None = None,
    channel_id: int | None = None,
    scene_value: str = "",
    unionid: str = "",
    external_contact_id: str = "",
    owner_staff_id: str = "",
    reason: str = "",
    request_json: dict[str, Any] | None = None,
    response_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        canonical_unionid = resolved_unionid(
            resolve_identity_with_dbapi(
                cur,
                ResolvePersonIdentityRequest(
                    unionid=text(unionid) or None,
                    external_userid=text(external_contact_id) or None,
                ),
            )
        )
        source_request = dict(request_json or {})
        if external_contact_id:
            source_request.setdefault("external_contact_id", text(external_contact_id))
        cur.execute(
            """
            INSERT INTO automation_channel_entry_effect_log (
                event_log_id, channel_id, scene_value, unionid, owner_staff_id,
                effect_type, idempotency_key, status, reason, request_json, response_json, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (effect_type, idempotency_key) DO UPDATE
            SET event_log_id = COALESCE(EXCLUDED.event_log_id, automation_channel_entry_effect_log.event_log_id),
                channel_id = COALESCE(EXCLUDED.channel_id, automation_channel_entry_effect_log.channel_id),
                scene_value = CASE WHEN EXCLUDED.scene_value <> '' THEN EXCLUDED.scene_value ELSE automation_channel_entry_effect_log.scene_value END,
                unionid = CASE WHEN EXCLUDED.unionid <> '' THEN EXCLUDED.unionid ELSE automation_channel_entry_effect_log.unionid END,
                owner_staff_id = CASE WHEN EXCLUDED.owner_staff_id <> '' THEN EXCLUDED.owner_staff_id ELSE automation_channel_entry_effect_log.owner_staff_id END,
                status = EXCLUDED.status,
                reason = EXCLUDED.reason,
                request_json = EXCLUDED.request_json,
                response_json = EXCLUDED.response_json,
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
            """,
            (
                event_log_id,
                channel_id,
                text(scene_value),
                canonical_unionid,
                text(owner_staff_id),
                text(effect_type),
                text(idempotency_key),
                text(status),
                text(reason),
                _json(source_request),
                _json(response_json or {}),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _with_effect_log_diagnostic_identity(dict(row)) if row else {}


def list_channel_entry_effect_logs(*, channel_id: int | None = None, scene_value: str = "", limit: int = 20) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if channel_id:
        clauses.append("channel_id = %s")
        params.append(int(channel_id))
    if text(scene_value):
        clauses.append("scene_value = %s")
        params.append(text(scene_value))
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(max(1, min(int(limit or 20), 100)))
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT * FROM automation_channel_entry_effect_log
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        return [_with_effect_log_diagnostic_identity(dict(row)) for row in cur.fetchall() or []]


def log_external_contact_event(
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
    with _connect() as conn, conn.cursor() as cur:
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
                text(corp_id),
                text(event_type),
                text(change_type),
                text(external_userid),
                text(user_id),
                int(event_time or 0),
                text(event_key),
                text(payload_xml),
                _json(payload_json),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}


def get_external_contact_event_log(event_log_id: int) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM wecom_external_contact_event_logs WHERE id = %s LIMIT 1", (int(event_log_id),))
        row = cur.fetchone()
        return dict(row) if row else None


def mark_event_status(event_log_id: int, status: str, error_message: str = "") -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE wecom_external_contact_event_logs
            SET process_status = %s, error_message = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (text(status), text(error_message), int(event_log_id)),
        )
        conn.commit()


def record_identity_sync_result(
    event_log_id: int,
    *,
    status: str,
    error_code: str = "",
    error_message: str = "",
    response_json: dict[str, Any] | None = None,
) -> None:
    with _connect() as conn, conn.cursor() as cur:
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
                text(status),
                text(error_code),
                text(error_message),
                _json(response_json or {}),
                int(event_log_id),
            ),
        )
        conn.commit()


def list_recent_events(scene_value: str, limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
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
            (text(scene_value), max(1, min(int(limit or 20), 100))),
        )
        return [dict(row) for row in cur.fetchall() or []]


def save_tag_snapshot(owner_staff_id: str, external_contact_id: str, tag_ids: list[str], tag_names: dict[str, str]) -> None:
    if not tag_ids:
        return
    with _connect() as conn, conn.cursor() as cur:
        unionid = resolve_external_userid_with_dbapi(cur, external_contact_id)
        if not unionid:
            _enqueue_tag_identity_resolution(cur, owner_staff_id=owner_staff_id, external_contact_id=external_contact_id, tag_ids=tag_ids, tag_names=tag_names)
            conn.commit()
            return
        for tag_id in tag_ids:
            cur.execute(
                """
                UPDATE contact_tags
                SET tag_name = %s,
                    created_at = CURRENT_TIMESTAMP
                WHERE unionid = %s
                  AND userid = %s
                  AND tag_id = %s
                """,
                (text(tag_names.get(tag_id)), unionid, text(owner_staff_id), text(tag_id)),
            )
            if cur.rowcount:
                continue
            cur.execute(
                """
                INSERT INTO contact_tags (unionid, userid, tag_id, tag_name, created_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (unionid, text(owner_staff_id), text(tag_id), text(tag_names.get(tag_id))),
            )
        conn.commit()


def _enqueue_tag_identity_resolution(
    cur,
    *,
    owner_staff_id: str,
    external_contact_id: str,
    tag_ids: list[str],
    tag_names: dict[str, str],
) -> None:
    external_userid = text(external_contact_id)
    if not external_userid:
        return
    source_key = f"{external_userid}:{text(owner_staff_id)}:contact_tags"
    cur.execute(
        """
        INSERT INTO crm_user_identity_resolution_queue (
            source_type,
            source_key,
            external_userid,
            payload_json,
            reason,
            status,
            first_seen_at,
            last_seen_at,
            created_at,
            updated_at
        ) VALUES (
            'contact_tags',
            %s,
            %s,
            %s,
            'missing_unionid',
            'pending',
            NOW(),
            NOW(),
            NOW(),
            NOW()
        )
        ON CONFLICT (source_type, source_key) WHERE status = 'pending' AND source_type <> '' AND source_key <> ''
        DO UPDATE SET
            external_userid = COALESCE(NULLIF(EXCLUDED.external_userid, ''), crm_user_identity_resolution_queue.external_userid),
            payload_json = crm_user_identity_resolution_queue.payload_json || EXCLUDED.payload_json,
            reason = EXCLUDED.reason,
            last_seen_at = NOW(),
            updated_at = NOW()
        RETURNING *
        """,
        (
            source_key,
            external_userid,
            _json(
                {
                    "owner_staff_id": text(owner_staff_id),
                    "external_contact_id": external_userid,
                    "tag_ids": [text(tag_id) for tag_id in tag_ids],
                    "tag_names": {text(key): text(value) for key, value in tag_names.items()},
                }
            ),
        ),
    )
    row = dict(cur.fetchone() or {})
    from aicrm_next.identity_contact.resolution_effects import plan_identity_resolution_effect

    plan_identity_resolution_effect(
        cur,
        row,
        source_route="channel_entry.contact_tags.identity_resolution",
    )


def decode_payload_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}
