from __future__ import annotations

from typing import Any

from aicrm_next.channels.channel_entry import repo as channel_entry_repo
from aicrm_next.channels.channel_entry.channel_write_port import SaveChannelRequest, build_channel_write_port

from .repo import channel_admin_uses_postgres, connect_channel_admin_db


def uses_postgres() -> bool:
    return channel_admin_uses_postgres()


def fetch_channel(channel_id: int) -> dict[str, Any] | None:
    conn = connect_channel_admin_db()
    if conn is None:
        return None
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.*,
                       active_asset.id AS active_qrcode_asset_id,
                       active_asset.status AS qrcode_status,
                       active_asset.qr_url AS active_qrcode_asset_url,
                       COALESCE(contact_stats.channel_contact_count, 0) AS channel_contact_count,
                       contact_stats.latest_channel_entered_at,
                       wca.customer_channel AS wca_customer_channel,
                       wca.link_url AS wca_link_url,
                       wca.final_url AS wca_final_url,
                       COALESCE(historical_scenes.historical_scene_values, '[]'::jsonb) AS historical_scene_values
                FROM automation_channel c
                LEFT JOIN LATERAL (
                    SELECT id, status, qr_url
                    FROM automation_channel_qrcode_asset qa
                    WHERE qa.channel_id = c.id
                      AND qa.status = 'active'
                    ORDER BY qa.generated_at DESC, qa.id DESC
                    LIMIT 1
                ) active_asset ON TRUE
                LEFT JOIN (
                    SELECT channel_id, count(*) AS channel_contact_count, max(last_channel_entered_at) AS latest_channel_entered_at
                    FROM automation_channel_contact
                    GROUP BY channel_id
                ) contact_stats ON contact_stats.channel_id = c.id
                LEFT JOIN wecom_customer_acquisition_links wca
                  ON wca.automation_channel_id = c.id AND wca.status = 'active'
                LEFT JOIN LATERAL (
                    SELECT jsonb_agg(a.scene_value ORDER BY a.updated_at DESC, a.id DESC) AS historical_scene_values
                    FROM automation_channel_scene_alias a
                    WHERE a.channel_id = c.id
                      AND a.scene_value <> c.scene_value
                      AND a.status <> 'revoked'
                    LIMIT 12
                ) historical_scenes ON TRUE
                WHERE c.id = %s
                """,
                (int(channel_id),),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def list_channels(*, limit: int, status: str = "", include_archived: bool = False) -> list[dict[str, Any]]:
    conn = connect_channel_admin_db()
    if conn is None:
        return []
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE c.status = %s"
        params.append(status)
    elif not include_archived:
        where = "WHERE (c.status IS NULL OR c.status <> 'archived')"
    params.append(limit)
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.*,
                       active_asset.id AS active_qrcode_asset_id,
                       active_asset.status AS qrcode_status,
                       active_asset.qr_url AS active_qrcode_asset_url,
                       COALESCE(contact_stats.channel_contact_count, 0) AS channel_contact_count,
                       contact_stats.latest_channel_entered_at,
                       wca.customer_channel AS wca_customer_channel,
                       wca.link_url AS wca_link_url,
                       wca.final_url AS wca_final_url,
                       COALESCE(historical_scenes.historical_scene_values, '[]'::jsonb) AS historical_scene_values
                FROM automation_channel c
                LEFT JOIN LATERAL (
                    SELECT id, status, qr_url
                    FROM automation_channel_qrcode_asset qa
                    WHERE qa.channel_id = c.id
                      AND qa.status = 'active'
                    ORDER BY qa.generated_at DESC, qa.id DESC
                    LIMIT 1
                ) active_asset ON TRUE
                LEFT JOIN (
                    SELECT channel_id, count(*) AS channel_contact_count, max(last_channel_entered_at) AS latest_channel_entered_at
                    FROM automation_channel_contact
                    GROUP BY channel_id
                ) contact_stats ON contact_stats.channel_id = c.id
                LEFT JOIN wecom_customer_acquisition_links wca
                  ON wca.automation_channel_id = c.id AND wca.status = 'active'
                LEFT JOIN LATERAL (
                    SELECT jsonb_agg(a.scene_value ORDER BY a.updated_at DESC, a.id DESC) AS historical_scene_values
                    FROM automation_channel_scene_alias a
                    WHERE a.channel_id = c.id
                      AND a.scene_value <> c.scene_value
                      AND a.status <> 'revoked'
                    LIMIT 12
                ) historical_scenes ON TRUE
                {where}
                ORDER BY c.updated_at DESC, c.id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            return [dict(row) for row in cur.fetchall() or []]


def save_channel(data: dict[str, Any], *, channel_id: int | None = None) -> int:
    conn = connect_channel_admin_db()
    if conn is None:
        return 0
    with conn:
        with conn.cursor() as cur:
            saved_id = build_channel_write_port().save(
                cur,
                request=SaveChannelRequest(data=data, channel_id=channel_id),
            )
        conn.commit()
    return saved_id


def list_channel_contacts(channel_id: int, *, limit: int) -> list[dict[str, Any]]:
    conn = connect_channel_admin_db()
    if conn is None:
        return []
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT external_contact_id, display_name, enter_count, last_channel_entered_at
                FROM automation_channel_contact
                WHERE channel_id = %s
                ORDER BY last_channel_entered_at DESC, id DESC
                LIMIT %s
                """,
                (int(channel_id), max(1, min(int(limit or 100), 500))),
            )
            return [dict(row) for row in cur.fetchall() or []]


def list_channel_welcome_materials(*, material_type: str, keyword_text: str) -> list[dict[str, Any]]:
    conn = connect_channel_admin_db()
    if conn is None:
        return []
    items: list[dict[str, Any]] = []
    with conn:
        with conn.cursor() as cur:
            if material_type in {"all", "miniprogram"}:
                cur.execute("SELECT id, name, title, appid, pagepath FROM miniprogram_library WHERE enabled = TRUE ORDER BY updated_at DESC, id DESC LIMIT 200")
                for row in cur.fetchall() or []:
                    haystack = " ".join(_text(row.get(key)) for key in ("name", "title", "appid", "pagepath")).lower()
                    if keyword_text and keyword_text not in haystack:
                        continue
                    name = _text(row.get("title") or row.get("name"))
                    items.append({"id": int(row["id"]), "type": "miniprogram", "name": name, "title": name, "description": _text(row.get("pagepath") or row.get("appid"))})
            if material_type in {"all", "image"}:
                cur.execute("SELECT id, name, file_name, mime_type FROM image_library WHERE enabled = TRUE ORDER BY updated_at DESC, id DESC LIMIT 200")
                for row in cur.fetchall() or []:
                    haystack = " ".join(_text(row.get(key)) for key in ("name", "file_name", "mime_type")).lower()
                    if keyword_text and keyword_text not in haystack:
                        continue
                    name = _text(row.get("name") or row.get("file_name"))
                    items.append({"id": int(row["id"]), "type": "image", "library": "image_library", "name": name, "title": name, "description": _text(row.get("file_name") or row.get("mime_type")), "mime_type": _text(row.get("mime_type"))})
            if material_type in {"all", "pdf"}:
                cur.execute("SELECT id, name, file_name, mime_type FROM attachment_library WHERE enabled = TRUE ORDER BY updated_at DESC, id DESC LIMIT 200")
                for row in cur.fetchall() or []:
                    mime = _text(row.get("mime_type")).lower()
                    file_name = _text(row.get("file_name"))
                    is_pdf = mime == "application/pdf" or file_name.lower().endswith(".pdf")
                    if not is_pdf:
                        continue
                    haystack = " ".join([_text(row.get("name")), file_name, mime]).lower()
                    if keyword_text and keyword_text not in haystack:
                        continue
                    name = _text(row.get("name") or file_name)
                    items.append({"id": int(row["id"]), "type": "pdf", "library": "attachment_library", "name": name, "title": name, "description": _text(file_name or mime), "mime_type": mime})
    return items


def normalize_channel_assignees(assignees: list[dict[str, Any]], *, strategy: str) -> list[dict[str, Any]]:
    return channel_entry_repo.normalize_channel_assignees(assignees, strategy=strategy)


def list_channel_assignees(channel_id: int, *, active_only: bool = False) -> list[dict[str, Any]]:
    return channel_entry_repo.list_channel_assignees(int(channel_id), active_only=active_only)


def list_assignment_stats_24h(channel_id: int) -> list[dict[str, Any]]:
    return channel_entry_repo.list_assignment_stats_24h(int(channel_id))


def save_channel_assignees(
    channel_id: int,
    *,
    assignment_mode: str,
    assignment_strategy: str,
    assignees: list[dict[str, Any]] | None,
    overflow_policy: str = "",
) -> dict[str, Any]:
    return channel_entry_repo.save_channel_assignees(
        int(channel_id),
        assignment_mode=assignment_mode,
        assignment_strategy=assignment_strategy,
        assignees=assignees,
        overflow_policy=overflow_policy,
    )


def list_assignment_events(channel_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    return channel_entry_repo.list_assignment_events(int(channel_id), limit=limit)


def choose_channel_assignee(
    channel_id: int,
    *,
    external_contact_id: str = "",
    wecom_user_id: str = "",
    write_event: bool = False,
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return channel_entry_repo.choose_channel_assignee(
        int(channel_id),
        external_contact_id=external_contact_id,
        wecom_user_id=wecom_user_id,
        write_event=write_event,
        source_payload=source_payload or {},
    )


def mark_qrcode_asset_stale(channel_id: int, *, reason: str) -> None:
    channel_entry_repo.mark_qrcode_asset_stale(int(channel_id), reason=reason)


def get_active_qrcode_asset(channel_id: int) -> dict[str, Any] | None:
    return channel_entry_repo.get_active_qrcode_asset(int(channel_id))


def list_channel_scene_aliases(channel_id: int) -> list[dict[str, Any]]:
    return channel_entry_repo.list_channel_scene_aliases(int(channel_id))


def list_channel_entry_effect_logs(*, channel_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return channel_entry_repo.list_channel_entry_effect_logs(channel_id=int(channel_id), limit=limit)


def list_recent_events(scene_value: str, *, limit: int = 10) -> list[dict[str, Any]]:
    return channel_entry_repo.list_recent_events(scene_value, limit=limit)


def _text(value: Any) -> str:
    return str(value or "").strip()
