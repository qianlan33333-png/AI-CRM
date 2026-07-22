from __future__ import annotations

from typing import Any

from aicrm_next.send_content_media_repository_gateway import build_send_content_media_repository
from aicrm_next.shared.repository_provider import RepositoryProviderError

from .repo import SendContentRepository, _assert_material_type, _clamp_limit


class PostgresSendContentRepository(SendContentRepository):
    source_status = "production_postgres_send_content"

    def __init__(self, database_url: str) -> None:
        self._media_repo = build_send_content_media_repository(database_url)

    def list_materials(
        self,
        material_type: str,
        *,
        q: str = "",
        enabled_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        _assert_material_type(material_type)
        limit = _clamp_limit(limit)
        offset = max(0, int(offset or 0))
        try:
            result = self._media_repo.list_items(
                material_type,
                limit=limit,
                offset=offset,
                filters={"q": q, "enabled_only": enabled_only},
            )
        except Exception as exc:
            raise RepositoryProviderError(f"send content production repository unavailable: {exc}") from exc
        return {
            "items": [self._to_picker_item(material_type, item) for item in result.get("items") or []],
            "total": int(result.get("total") or 0),
            "limit": limit,
            "offset": offset,
        }

    def get_materials_by_ids(self, material_type: str, ids: list[int]) -> list[dict[str, Any]]:
        _assert_material_type(material_type)
        rows: list[dict[str, Any]] = []
        for item_id in ids:
            try:
                item = self._media_repo.get_item(material_type, str(item_id), include_data=False)
            except Exception as exc:
                raise RepositoryProviderError(f"send content production repository unavailable: {exc}") from exc
            if item:
                rows.append(self._to_picker_item(material_type, item))
        by_id = {int(item["library_id"]): item for item in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def list_material_asset_usage(
        self,
        material_type: str,
        source_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        _assert_material_type(material_type)
        limit = _clamp_limit(limit)
        offset = max(0, int(offset or 0))
        source_id = int(source_id)
        try:
            with self._media_repo._connect() as conn:
                rows: list[dict[str, Any]] = []
                rows.extend(_channel_welcome_usage(conn, material_type, source_id))
                rows.extend(_automation_agent_usage(conn, material_type, source_id))
                rows.extend(_cloud_plan_usage(conn, material_type, source_id))
                rows.extend(_group_ops_usage(conn, material_type, source_id))
                rows.extend(_radar_link_usage(conn, material_type, source_id))
                rows.extend(_wechat_pay_product_slice_usage(conn, material_type, source_id))
        except Exception as exc:
            raise RepositoryProviderError(f"send content material usage repository unavailable: {exc}") from exc
        rows.sort(key=lambda item: (str(item.get("used_at") or ""), str(item.get("usage_id") or "")), reverse=True)
        return {"items": rows[offset : offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    def _to_picker_item(self, material_type: str, item: dict[str, Any]) -> dict[str, Any]:
        if material_type == "image":
            library_id = int(item.get("id") or 0)
            title = str(item.get("name") or item.get("file_name") or f"图片素材 {library_id}")
            thumbnail_url = str(
                item.get("thumb_160_url")
                or item.get("thumb_url")
                or item.get("thumb_320_url")
                or f"/api/admin/image-library/{library_id}/thumbnail?size=160"
            )
            mime_type = str(item.get("mime_type") or item.get("content_type") or "")
            file_size = int(item.get("file_size") or 0)
            return {
                "type": "image",
                "library_id": library_id,
                "title": title,
                "subtitle": _join_subtitle(mime_type, _format_file_size(file_size)),
                "thumbnail_url": thumbnail_url,
                "enabled": bool(item.get("enabled", True)),
                "metadata": {
                    "file_name": str(item.get("file_name") or ""),
                    "mime_type": mime_type,
                    "category": str(item.get("category") or ""),
                    "tags": list(item.get("tags") or []),
                },
            }
        if material_type == "miniprogram":
            library_id = int(item.get("id") or 0)
            thumb_image_id = item.get("thumb_image_id")
            fallback = f"/api/admin/image-library/{thumb_image_id}/thumbnail?size=160" if thumb_image_id not in (None, "") else ""
            thumbnail_url = str(
                item.get("thumb_160_url")
                or item.get("thumb_url")
                or item.get("thumb_image_url")
                or fallback
            )
            appid = str(item.get("appid") or "")
            pagepath = str(item.get("pagepath") or item.get("page_path") or "")
            return {
                "type": "miniprogram",
                "library_id": library_id,
                "title": str(item.get("title") or item.get("name") or f"小程序素材 {library_id}"),
                "subtitle": _join_subtitle(appid, pagepath),
                "thumbnail_url": thumbnail_url,
                "enabled": bool(item.get("enabled", True)),
                "metadata": {
                    "appid": appid,
                    "pagepath": pagepath,
                    "thumb_image_id": thumb_image_id,
                    "thumb_media_id": str(item.get("thumb_media_id") or ""),
                },
            }
        if material_type == "group_invite":
            library_id = int(item.get("id") or 0)
            title = str(item.get("title") or item.get("name") or f"客户群 {library_id}")
            description = str(item.get("description") or "")
            pic_url = str(item.get("pic_url") or "")
            binding_status = str(item.get("binding_status") or ("ready" if item.get("join_url") else "pending"))
            return {
                "type": "group_invite",
                "library_id": library_id,
                "title": title,
                "subtitle": "选用时由系统自动生成邀请" if binding_status == "pending" else "群邀请已失效" if binding_status == "invalid" else description or "点击卡片直接加入群聊",
                "thumbnail_url": pic_url,
                "enabled": bool(item.get("enabled", True)),
                "metadata": {
                    "description": description,
                    "join_url": str(item.get("join_url") or ""),
                    "pic_url": pic_url,
                    "config_id": str(item.get("config_id") or ""),
                    "state": str(item.get("state") or ""),
                    "binding_status": binding_status,
                    "chat_id": str(item.get("chat_id") or ((item.get("chat_id_list") or [""])[0]) or ""),
                    "chat_id_list": list(item.get("chat_id_list") or []),
                    "auto_create_room": bool(item.get("auto_create_room", False)),
                    "room_base_name": str(item.get("room_base_name") or ""),
                    "room_base_id": item.get("room_base_id"),
                },
            }
        library_id = int(item.get("id") or 0)
        mime_type = str(item.get("mime_type") or "")
        file_size = int(item.get("file_size") or 0)
        return {
            "type": "attachment",
            "library_id": library_id,
            "title": str(item.get("name") or item.get("file_name") or f"附件素材 {library_id}"),
            "subtitle": _join_subtitle(mime_type, _format_file_size(file_size)),
            "thumbnail_url": "",
            "enabled": bool(item.get("enabled", True)),
            "metadata": {
                "file_name": str(item.get("file_name") or ""),
                "mime_type": mime_type,
                "file_size": file_size,
                "tags": list(item.get("tags") or []),
            },
        }


def _join_subtitle(*parts: str) -> str:
    return " · ".join(part for part in (str(part or "").strip() for part in parts) if part)


def _format_file_size(size: int) -> str:
    if size <= 0:
        return ""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)}KB"
    return f"{round(size / 1024 / 1024, 1)}MB"


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (f"public.{table_name}",)).fetchone()
    return bool((row or {}).get("exists"))


def _columns_exist(conn: Any, table_name: str, columns: tuple[str, ...]) -> bool:
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = ANY(%s)
        """,
        (table_name, list(columns)),
    ).fetchall()
    found = {str(row.get("column_name") or "") for row in rows or []}
    return set(columns).issubset(found)


def _json_array_contains_expr(column_name: str, key: str = "") -> str:
    source = f"{column_name} -> %s" if key else f"to_jsonb({column_name})"
    return f"""
        EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(COALESCE({source}, '[]'::jsonb)) AS material_id(value)
            WHERE material_id.value = %s
        )
    """


def _usage_item(
    *,
    material_type: str,
    source_id: int,
    consumer_type: str,
    source_table: str,
    source_record_id: str,
    title: str,
    status: str,
    field_path: str,
    owner_userid: str = "",
    used_at: Any = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    material_asset_id = f"{material_type}:{int(source_id)}"
    return {
        "usage_id": f"{consumer_type}:{source_table}:{source_record_id}:{field_path}",
        "material_asset_id": material_asset_id,
        "asset_type": material_type,
        "source_id": int(source_id),
        "consumer_type": consumer_type,
        "source_table": source_table,
        "source_record_id": str(source_record_id),
        "title": str(title or ""),
        "status": str(status or ""),
        "owner_userid": str(owner_userid or ""),
        "field_path": str(field_path or ""),
        "used_at": str(used_at or ""),
        "metadata": metadata or {},
    }


def _channel_welcome_usage(conn: Any, material_type: str, source_id: int) -> list[dict[str, Any]]:
    field_by_type = {
        "image": "welcome_image_library_ids",
        "miniprogram": "welcome_miniprogram_library_ids",
        "attachment": "welcome_attachment_library_ids",
        "group_invite": "welcome_group_invite_library_ids",
    }
    field_name = field_by_type[material_type]
    columns = ("id", "channel_code", "channel_name", "status", "owner_staff_id", field_name, "updated_at")
    if not _columns_exist(conn, "automation_channel", columns):
        return []
    rows = conn.execute(
        f"""
        SELECT id, channel_code, channel_name, status, owner_staff_id, updated_at
        FROM automation_channel
        WHERE {_json_array_contains_expr(field_name)}
        ORDER BY updated_at DESC, id DESC
        LIMIT 200
        """,
        (str(source_id),),
    ).fetchall()
    return [
        _usage_item(
            material_type=material_type,
            source_id=source_id,
            consumer_type="channel_welcome_config",
            source_table="automation_channel",
            source_record_id=str(row.get("id") or ""),
            title=str(row.get("channel_name") or row.get("channel_code") or "渠道欢迎语"),
            status=str(row.get("status") or ""),
            owner_userid=str(row.get("owner_staff_id") or ""),
            field_path=field_name,
            used_at=row.get("updated_at"),
        )
        for row in rows or []
    ]


def _automation_agent_usage(conn: Any, material_type: str, source_id: int) -> list[dict[str, Any]]:
    if not _columns_exist(conn, "automation_agents", ("id", "agent_code", "agent_name", "status", "fixed_content_package_json", "updated_at")):
        return []
    key = _package_key(material_type)
    rows = conn.execute(
        f"""
        SELECT id, agent_code, agent_name, status, updated_at
        FROM automation_agents
        WHERE {_json_array_contains_expr("fixed_content_package_json", key)}
        ORDER BY updated_at DESC, id DESC
        LIMIT 200
        """,
        (key, str(source_id)),
    ).fetchall()
    return [
        _usage_item(
            material_type=material_type,
            source_id=source_id,
            consumer_type="send_content_package",
            source_table="automation_agents",
            source_record_id=str(row.get("id") or ""),
            title=str(row.get("agent_name") or row.get("agent_code") or "自动化固定内容包"),
            status=str(row.get("status") or ""),
            field_path=f"fixed_content_package_json.{key}",
            used_at=row.get("updated_at"),
        )
        for row in rows or []
    ]


def _cloud_plan_usage(conn: Any, material_type: str, source_id: int) -> list[dict[str, Any]]:
    columns = ("id", "plan_id", "content_text", "content_payload_json", "status", "updated_at")
    if not _columns_exist(conn, "cloud_broadcast_plan_recipient_messages", columns):
        return []
    key = _package_key(material_type)
    rows = conn.execute(
        f"""
        SELECT id, plan_id, content_text, status, updated_at
        FROM cloud_broadcast_plan_recipient_messages
        WHERE {_json_array_contains_expr("content_payload_json", key)}
        ORDER BY updated_at DESC, id DESC
        LIMIT 200
        """,
        (key, str(source_id)),
    ).fetchall()
    return [
        _usage_item(
            material_type=material_type,
            source_id=source_id,
            consumer_type="cloud_plan_content_payload",
            source_table="cloud_broadcast_plan_recipient_messages",
            source_record_id=str(row.get("id") or ""),
            title=str(row.get("content_text") or row.get("plan_id") or "云群发计划话术"),
            status=str(row.get("status") or ""),
            field_path=f"content_payload_json.{key}",
            used_at=row.get("updated_at"),
            metadata={"plan_id": str(row.get("plan_id") or "")},
        )
        for row in rows or []
    ]


def _group_ops_usage(conn: Any, material_type: str, source_id: int) -> list[dict[str, Any]]:
    columns = ("id", "plan_id", "action_title", "status", "content_package_json", "updated_at")
    if not _columns_exist(conn, "automation_group_ops_plan_nodes", columns):
        return []
    key = _package_key(material_type)
    rows = conn.execute(
        f"""
        SELECT id, plan_id, action_title, status, updated_at
        FROM automation_group_ops_plan_nodes
        WHERE {_json_array_contains_expr("content_package_json", key)}
        ORDER BY updated_at DESC, id DESC
        LIMIT 200
        """,
        (key, str(source_id)),
    ).fetchall()
    return [
        _usage_item(
            material_type=material_type,
            source_id=source_id,
            consumer_type="group_ops_draft",
            source_table="automation_group_ops_plan_nodes",
            source_record_id=str(row.get("id") or ""),
            title=str(row.get("action_title") or "群运营节点"),
            status=str(row.get("status") or ""),
            field_path=f"content_package_json.{key}",
            used_at=row.get("updated_at"),
            metadata={"plan_id": str(row.get("plan_id") or "")},
        )
        for row in rows or []
    ]


def _radar_link_usage(conn: Any, material_type: str, source_id: int) -> list[dict[str, Any]]:
    if not _columns_exist(conn, "radar_links", ("id", "title", "target_type", "media_item_id", "enabled", "updated_at", "deleted_at")):
        return []
    if material_type not in {"image", "attachment"}:
        return []
    rows = conn.execute(
        """
        SELECT id, title, target_type, enabled, updated_at
        FROM radar_links
        WHERE deleted_at IS NULL
          AND media_item_id IN (%s, %s)
        ORDER BY updated_at DESC, id DESC
        LIMIT 200
        """,
        (str(source_id), f"{material_type}:{source_id}"),
    ).fetchall()
    return [
        _usage_item(
            material_type=material_type,
            source_id=source_id,
            consumer_type="radar_link",
            source_table="radar_links",
            source_record_id=str(row.get("id") or ""),
            title=str(row.get("title") or "内容雷达"),
            status="enabled" if bool(row.get("enabled")) else "disabled",
            field_path="media_item_id",
            used_at=row.get("updated_at"),
            metadata={"target_type": str(row.get("target_type") or "")},
        )
        for row in rows or []
    ]


def _wechat_pay_product_slice_usage(conn: Any, material_type: str, source_id: int) -> list[dict[str, Any]]:
    if material_type != "image":
        return []
    if not _columns_exist(conn, "wechat_pay_product_page_slices", ("id", "product_id", "image_library_id", "enabled", "updated_at")):
        return []
    rows = conn.execute(
        """
        SELECT id, product_id, enabled, updated_at
        FROM wechat_pay_product_page_slices
        WHERE image_library_id = %s
        ORDER BY updated_at DESC, id DESC
        LIMIT 200
        """,
        (int(source_id),),
    ).fetchall()
    return [
        _usage_item(
            material_type=material_type,
            source_id=source_id,
            consumer_type="wechat_pay_product_page_slice",
            source_table="wechat_pay_product_page_slices",
            source_record_id=str(row.get("id") or ""),
            title=f"微信支付商品页 {row.get('product_id') or ''}".strip(),
            status="enabled" if bool(row.get("enabled")) else "disabled",
            field_path="image_library_id",
            used_at=row.get("updated_at"),
            metadata={"product_id": str(row.get("product_id") or "")},
        )
        for row in rows or []
    ]


def _package_key(material_type: str) -> str:
    return {
        "image": "image_library_ids",
        "miniprogram": "miniprogram_library_ids",
        "attachment": "attachment_library_ids",
        "group_invite": "group_invite_library_ids",
    }[material_type]
