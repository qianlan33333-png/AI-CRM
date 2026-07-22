from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.safe_logging import safe_log_exception

from .dto import normalize_group_invite_join_url, normalize_http_url
from .repo import connect_media_library_db, normalize_tags
from .variants import (
    THUMBNAIL_SIZE_TO_VARIANT,
    add_image_variant_urls,
    decode_image_base64,
    generate_image_variants,
    make_thumbnail_bytes,
    variant_bytes,
)


logger = logging.getLogger(__name__)


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return default


def _iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "t"}


def _coerce_optional_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ContractError(f"{field_name} must be numeric")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    raise ContractError(f"{field_name} must be numeric")


class PostgresMediaLibraryRepository:
    source_status = "production_postgres_media_library"

    def __init__(self, database_url: str) -> None:
        self._database_url = _psycopg_url(database_url)
        self._variants_table_available: bool | None = None
        self._table_columns_cache: dict[str, set[str]] = {}

    def _connect(self):
        return connect_media_library_db(self._database_url)

    def list_items(self, kind: str, *, limit: int, offset: int, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = filters or {}
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))
        if kind == "image":
            return self._list_images(limit=limit, offset=offset, filters=filters)
        if kind == "miniprogram":
            return self._list_miniprograms(limit=limit, offset=offset, filters=filters)
        if kind == "group_invite":
            return self._list_group_invites(limit=limit, offset=offset, filters=filters)
        return self._list_attachments(limit=limit, offset=offset, filters=filters)

    def list_facets(self, kind: str) -> dict[str, list[str]]:
        if kind != "image":
            return {"categories": [], "tags": []}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT category, tags FROM image_library WHERE enabled")
                categories: set[str] = set()
                tags: set[str] = set()
                for row in cur.fetchall() or []:
                    category = str(row.get("category") or "").strip()
                    if category:
                        categories.add(category)
                    for tag in normalize_tags(_json(row.get("tags"), [])):
                        tags.add(tag)
                return {"categories": sorted(categories), "tags": sorted(tags)}

    def get_item(self, kind: str, item_id: str, *, include_data: bool = True) -> dict[str, Any] | None:
        table = self._table(kind)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {table} WHERE id = %s", (int(item_id),))
                row = cur.fetchone()
        if not row:
            return None
        return self._serialize(kind, dict(row), include_data=include_data)

    def get_image_variant(self, image_id: str, variant_key: str) -> dict[str, Any] | None:
        if variant_key not in {"original", "thumb_160", "thumb_320", "preview_720", "mobile_1080", "large_1440"}:
            return None
        image_id_int = int(image_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not self._image_variants_table_exists(cur):
                    return None
                variant = self._fetch_variant(cur, image_id_int, variant_key)
                if not variant:
                    cur.execute("SELECT * FROM image_library WHERE id = %s", (image_id_int,))
                    image = cur.fetchone()
                    if not image:
                        return None
                    self._ensure_image_variants(cur, dict(image))
                    variant = self._fetch_variant(cur, image_id_int, variant_key)
                conn.commit()
        if not variant:
            return None
        payload = variant_bytes(variant)
        return {**variant, "bytes": payload, "etag": '"' + str(variant.get("checksum") or "") + '"'}

    def get_image_thumbnail(self, image_id: str, size: int) -> dict[str, Any] | None:
        if size not in THUMBNAIL_SIZE_TO_VARIANT:
            raise ContractError("thumbnail size must be one of 160, 320, 720")
        try:
            image_id_int = int(image_id)
        except (TypeError, ValueError):
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                if self._image_variants_table_exists(cur):
                    variant = self._fetch_variant(cur, image_id_int, THUMBNAIL_SIZE_TO_VARIANT[size])
                    if variant and str(variant.get("mime_type") or "").split(";")[0] in {"image/png", "image/jpeg"}:
                        payload = variant_bytes(variant)
                        return {**variant, "bytes": payload, "etag": '"' + str(variant.get("checksum") or "") + '"'}
                cur.execute("SELECT id, data_base64, mime_type, source_url FROM image_library WHERE id = %s", (image_id_int,))
                image = cur.fetchone()
        if not image:
            return None
        data_base64 = str(image.get("data_base64") or "")
        mime_type = str(image.get("mime_type") or "image/png")
        if data_base64:
            data = decode_image_base64(data_base64)
        elif image.get("source_url"):
            raise ContractError("remote source fetch is disabled in Next media library")
        else:
            data = b""
        return make_thumbnail_bytes(
            image_id=image_id_int,
            data=data,
            mime_type=mime_type,
            size=size,
        )

    def save_item(self, kind: str, payload: dict[str, Any], item_id: str | None = None) -> dict[str, Any]:
        if kind == "image":
            return self._save_image(payload, item_id)
        if kind == "miniprogram":
            return self._save_miniprogram(payload, item_id)
        if kind == "group_invite":
            return self._save_group_invite(payload, item_id)
        return self._save_attachment(payload, item_id)

    def delete_item(self, kind: str, item_id: str, *, force: bool = False) -> dict[str, Any]:
        if kind == "image":
            return self._delete_image(item_id, force=force)
        table = self._table(kind)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {table} WHERE id = %s", (int(item_id),))
                deleted = (cur.rowcount or 0) > 0
            conn.commit()
        if not deleted:
            raise NotFoundError(f"{kind} item not found")
        return {"ok": True, "deleted": True, "id": int(item_id)}

    def cache_image_media_id(self, item_id: str, media_id: str, expires_at: datetime) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE image_library
                    SET thumb_media_id = %s,
                        thumb_media_id_expires_at = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (media_id, expires_at, int(item_id)),
                )
            conn.commit()

    def cache_miniprogram_thumb_media_id(self, item_id: str, media_id: str, expires_at: datetime) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE miniprogram_library
                    SET thumb_media_id = %s,
                        thumb_media_id_expires_at = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (media_id, expires_at, int(item_id)),
                )
            conn.commit()

    def cache_attachment_media_id(self, item_id: str, media_id: str, expires_at: datetime) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE attachment_library
                    SET media_id = %s,
                        media_id_expires_at = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (media_id, expires_at, int(item_id)),
                )
            conn.commit()

    def repair_missing_miniprogram_thumb_sources(
        self,
        *,
        execute: bool = False,
        explicit_bindings: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        bindings = {int(material_id): int(image_id) for material_id, image_id in (explicit_bindings or {}).items()}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH deterministic AS (
                        SELECT gap.id AS material_id, MIN(source.thumb_image_id) AS image_id
                        FROM miniprogram_library gap
                        JOIN miniprogram_library source
                          ON COALESCE(source.appid, '') = COALESCE(gap.appid, '')
                         AND COALESCE(source.pagepath, '') = COALESCE(gap.pagepath, '')
                         AND COALESCE(source.title, '') = COALESCE(gap.title, '')
                         AND source.thumb_image_id IS NOT NULL
                        WHERE gap.enabled IS TRUE
                          AND gap.thumb_image_id IS NULL
                          AND COALESCE(gap.thumb_image_base64, '') = ''
                        GROUP BY gap.id
                        HAVING COUNT(DISTINCT source.thumb_image_id) = 1
                    )
                    SELECT material_id, image_id FROM deterministic ORDER BY material_id
                    """
                )
                deterministic = {int(row["material_id"]): int(row["image_id"]) for row in cur.fetchall() or []}
                for material_id, image_id in bindings.items():
                    cur.execute(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM miniprogram_library
                            WHERE id = %s AND enabled IS TRUE AND thumb_image_id IS NULL
                              AND COALESCE(thumb_image_base64, '') = ''
                        ) AS material_valid,
                        EXISTS (
                            SELECT 1 FROM image_library
                            WHERE id = %s AND enabled IS TRUE AND COALESCE(data_base64, '') <> ''
                        ) AS image_valid
                        """,
                        (material_id, image_id),
                    )
                    validation = cur.fetchone() or {}
                    if not validation.get("material_valid") or not validation.get("image_valid"):
                        raise ContractError(f"invalid miniprogram thumb binding {material_id}:{image_id}")
                planned = {**deterministic, **bindings}
                if execute and planned:
                    cur.executemany(
                        """
                        UPDATE miniprogram_library
                        SET thumb_image_id = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND thumb_image_id IS NULL
                          AND COALESCE(thumb_image_base64, '') = ''
                        """,
                        [(image_id, material_id) for material_id, image_id in sorted(planned.items())],
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS unresolved_count
                    FROM miniprogram_library
                    WHERE enabled IS TRUE AND thumb_image_id IS NULL
                      AND COALESCE(thumb_image_base64, '') = ''
                    """
                )
                unresolved_count = int((cur.fetchone() or {}).get("unresolved_count") or 0)
            if execute:
                conn.commit()
            else:
                conn.rollback()
        return {
            "execute": bool(execute),
            "deterministic_count": len(deterministic),
            "explicit_count": len(bindings),
            "planned_count": len(planned),
            "updated_count": len(planned) if execute else 0,
            "unresolved_count": unresolved_count,
            "bindings": [{"material_id": key, "image_id": value} for key, value in sorted(planned.items())],
        }

    def _list_images(self, *, limit: int, offset: int, filters: dict[str, Any]) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if filters.get("enabled_only") is not False:
            where.append("enabled")
        q = str(filters.get("q") or "").strip()
        if q:
            where.append("(name ILIKE %s OR file_name ILIKE %s OR description ILIKE %s)")
            like = f"%{q}%"
            params.extend([like, like, like])
        category = str(filters.get("category") or "").strip()
        if category:
            where.append("category = %s")
            params.append(category)
        tags = normalize_tags(filters.get("tags"))
        if tags:
            where.append("EXISTS (SELECT 1 FROM jsonb_array_elements_text(tags) AS tag WHERE tag = ANY(%s))")
            params.append(tags)
        if filters.get("only_unlabeled"):
            where.append("(description = '' OR category = '' OR jsonb_array_length(tags) = 0)")
        return self._select_list(
            "image",
            "image_library",
            where,
            params,
            "updated_at DESC, id DESC",
            limit=limit,
            offset=offset,
        )

    def _list_miniprograms(self, *, limit: int, offset: int, filters: dict[str, Any]) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if filters.get("enabled_only") is not False:
            where.append("enabled")
        q = str(filters.get("q") or "").strip()
        if q:
            where.append("(name ILIKE %s OR title ILIKE %s OR appid ILIKE %s OR pagepath ILIKE %s)")
            like = f"%{q}%"
            params.extend([like, like, like, like])
        return self._select_list("miniprogram", "miniprogram_library", where, params, "updated_at DESC, id DESC", limit=limit, offset=offset)

    def _list_attachments(self, *, limit: int, offset: int, filters: dict[str, Any]) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if filters.get("enabled_only") is not False:
            where.append("enabled")
        q = str(filters.get("q") or "").strip()
        if q:
            where.append("(name ILIKE %s OR file_name ILIKE %s OR mime_type ILIKE %s OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(tags) AS tag WHERE tag ILIKE %s))")
            like = f"%{q}%"
            params.extend([like, like, like, like])
        return self._select_list("attachment", "attachment_library", where, params, "updated_at DESC, id DESC", limit=limit, offset=offset)

    def _list_group_invites(self, *, limit: int, offset: int, filters: dict[str, Any]) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if filters.get("enabled_only") is not False:
            where.append("enabled")
        q = str(filters.get("q") or "").strip()
        if q:
            where.append("(name ILIKE %s OR title ILIKE %s OR description ILIKE %s OR join_url ILIKE %s OR config_id ILIKE %s)")
            like = f"%{q}%"
            params.extend([like, like, like, like, like])
        return self._select_list(
            "group_invite",
            "group_invite_library",
            where,
            params,
            "updated_at DESC, id DESC",
            limit=limit,
            offset=offset,
        )

    def _select_list(self, kind: str, table: str, where: list[str], params: list[Any], order_by: str, *, limit: int, offset: int) -> dict[str, Any]:
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) AS total FROM {table}{where_sql}", tuple(params))
                total = int((cur.fetchone() or {}).get("total") or 0)
                available_columns = self._table_columns(cur, table)
                cur.execute(
                    f"SELECT {self._list_columns(kind, available_columns)} FROM {table}{where_sql} ORDER BY {order_by} LIMIT %s OFFSET %s",
                    tuple(params + [limit, offset]),
                )
                raw_rows = [dict(row) for row in cur.fetchall() or []]
                use_thumbnail_fallback = kind in {"image", "miniprogram"} and not self._image_variants_table_exists(cur)
                rows = [
                    self._serialize(kind, row, include_data=False, use_thumbnail_fallback=use_thumbnail_fallback)
                    for row in raw_rows
                ]
                if kind == "image":
                    self._attach_image_variant_dimensions(cur, rows)
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def _table_columns(self, cur: Any, table: str) -> set[str] | None:
        if table in self._table_columns_cache:
            return self._table_columns_cache[table]
        try:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = %s
                """,
                (table,),
            )
            columns = {str(row.get("column_name") or "").strip() for row in cur.fetchall() or []}
        except Exception as exc:
            safe_log_exception(
                logger,
                "media library table column probe failed",
                exc,
                level=logging.DEBUG,
                table=table,
            )
            return None
        columns.discard("")
        if not columns:
            return None
        self._table_columns_cache[table] = columns
        return columns

    def _list_columns(self, kind: str, available_columns: set[str] | None = None) -> str:
        def column(name: str, fallback_sql: str) -> str:
            if available_columns is None or name in available_columns:
                return name
            return f"{fallback_sql} AS {name}"

        if kind == "image":
            fields = [
                ("id", "NULL::integer"),
                ("name", "''"),
                ("file_name", "''"),
                ("source", "'upload'"),
                ("source_url", "''"),
                ("mime_type", "'image/png'"),
                ("file_size", "0"),
                ("thumb_media_id", "''"),
                ("thumb_media_id_expires_at", "NULL::timestamp"),
                ("enabled", "TRUE"),
                ("description", "''"),
                ("tags", "'[]'::jsonb"),
                ("category", "''"),
                ("ai_metadata", "'{}'::jsonb"),
                ("width", "0"),
                ("height", "0"),
                ("created_at", "NULL::timestamp"),
                ("updated_at", "NULL::timestamp"),
            ]
            return ", ".join(column(name, fallback) for name, fallback in fields)
        if kind == "miniprogram":
            fields = [
                ("id", "NULL::integer"),
                ("name", "''"),
                ("appid", "''"),
                ("pagepath", "''"),
                ("title", "''"),
                ("thumb_image_id", "NULL::integer"),
                ("thumb_media_id", "''"),
                ("thumb_media_id_expires_at", "NULL::timestamp"),
                ("thumb_image_url", "''"),
                ("enabled", "TRUE"),
                ("created_at", "NULL::timestamp"),
                ("updated_at", "NULL::timestamp"),
            ]
            return ", ".join(column(name, fallback) for name, fallback in fields)
        if kind == "group_invite":
            fields = [
                ("id", "NULL::integer"),
                ("name", "''"),
                ("title", "''"),
                ("description", "''"),
                ("pic_url", "''"),
                ("join_url", "''"),
                ("config_id", "''"),
                ("state", "''"),
                ("chat_id_list", "'[]'::jsonb"),
                ("chat_id", "''"),
                ("binding_status", "'ready'"),
                ("auto_create_room", "TRUE"),
                ("room_base_name", "''"),
                ("room_base_id", "NULL::integer"),
                ("enabled", "TRUE"),
                ("created_at", "NULL::timestamp"),
                ("updated_at", "NULL::timestamp"),
            ]
            return ", ".join(column(name, fallback) for name, fallback in fields)
        fields = [
            ("id", "NULL::integer"),
            ("name", "''"),
            ("file_name", "''"),
            ("mime_type", "'application/octet-stream'"),
            ("file_size", "0"),
            ("media_id", "''"),
            ("media_id_expires_at", "NULL::timestamp"),
            ("tags", "'[]'::jsonb"),
            ("enabled", "TRUE"),
            ("created_at", "NULL::timestamp"),
            ("updated_at", "NULL::timestamp"),
        ]
        return ", ".join(column(name, fallback) for name, fallback in fields)

    def _image_variants_table_exists(self, cur: Any) -> bool:
        if self._variants_table_available is not None:
            return self._variants_table_available
        try:
            cur.execute("SELECT to_regclass('public.image_library_variants') AS table_name")
            self._variants_table_available = bool((cur.fetchone() or {}).get("table_name"))
        except Exception as exc:
            safe_log_exception(
                logger,
                "image_library_variants table availability check failed",
                exc,
                level=logging.DEBUG,
            )
            self._variants_table_available = False
        return self._variants_table_available

    def _attach_image_variant_dimensions(self, cur: Any, rows: list[dict[str, Any]]) -> None:
        image_ids = [int(item["id"]) for item in rows if item.get("id")]
        if not image_ids:
            return
        if not self._image_variants_table_exists(cur):
            return
        try:
            cur.execute(
                """
                SELECT image_id, width, height
                FROM image_library_variants
                WHERE variant_key = 'original' AND image_id = ANY(%s)
                """,
                (image_ids,),
            )
            by_id = {int(row["image_id"]): row for row in cur.fetchall() or []}
        except Exception as exc:
            safe_log_exception(logger, "image variant dimensions unavailable", exc, level=logging.DEBUG)
            return
        for item in rows:
            variant = by_id.get(int(item.get("id") or 0)) or {}
            item["width"] = int(variant.get("width") or item.get("width") or 0)
            item["height"] = int(variant.get("height") or item.get("height") or 0)

    def _fetch_variant(self, cur: Any, image_id: int, variant_key: str) -> dict[str, Any] | None:
        if not self._image_variants_table_exists(cur):
            return None
        cur.execute(
            """
            SELECT image_id, variant_key, storage_backend, storage_key, public_url,
                   mime_type, width, height, file_size, checksum, data_base64,
                   created_at, updated_at
            FROM image_library_variants
            WHERE image_id = %s AND variant_key = %s
            """,
            (image_id, variant_key),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def _ensure_image_variants(self, cur: Any, image: dict[str, Any]) -> None:
        if not self._image_variants_table_exists(cur):
            return
        image_id = int(image.get("id") or 0)
        variants = generate_image_variants(
            image_id=image_id,
            data_base64=str(image.get("data_base64") or ""),
            mime_type=str(image.get("mime_type") or "image/png"),
        )
        for variant in variants.values():
            cur.execute(
                """
                INSERT INTO image_library_variants
                    (image_id, variant_key, storage_backend, storage_key, public_url,
                     mime_type, width, height, file_size, checksum, data_base64)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (image_id, variant_key) DO UPDATE SET
                    storage_backend = EXCLUDED.storage_backend,
                    storage_key = EXCLUDED.storage_key,
                    public_url = EXCLUDED.public_url,
                    mime_type = EXCLUDED.mime_type,
                    width = EXCLUDED.width,
                    height = EXCLUDED.height,
                    file_size = EXCLUDED.file_size,
                    checksum = EXCLUDED.checksum,
                    data_base64 = EXCLUDED.data_base64,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    image_id,
                    variant.variant_key,
                    variant.storage_backend,
                    variant.storage_key,
                    variant.public_url,
                    variant.mime_type,
                    variant.width,
                    variant.height,
                    variant.file_size,
                    variant.checksum,
                    variant.data_base64,
                ),
            )

    def _save_image(self, payload: dict[str, Any], item_id: str | None) -> dict[str, Any]:
        data_base64 = str(payload.get("data_base64") or "").strip()
        if not data_base64 and payload.get("data_url"):
            _, _, data_base64 = str(payload.get("data_url") or "").partition(",")
        data = {
            "name": str(payload.get("name") or payload.get("file_name") or "图片素材").strip()[:200],
            "file_name": str(payload.get("file_name") or "image.png").strip()[:200],
            "source": str(payload.get("source") or "upload").strip()[:40],
            "source_url": str(payload.get("source_url") or "").strip()[:1000],
            "data_base64": data_base64,
            "mime_type": str(payload.get("mime_type") or payload.get("content_type") or "image/png").strip()[:80],
            "file_size": int(payload.get("file_size") or 0),
            "enabled": _bool(payload.get("enabled", True)),
            "description": str(payload.get("description") or "").strip()[:4000],
            "tags": normalize_tags(payload.get("tags")),
            "category": str(payload.get("category") or "").strip()[:80],
            "ai_metadata": payload.get("ai_metadata") if isinstance(payload.get("ai_metadata"), dict) else {},
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                if item_id:
                    sets = []
                    params = []
                    for key in ["name", "description", "tags", "category", "enabled", "ai_metadata"]:
                        if key in payload:
                            sets.append(f"{key} = %s::jsonb" if key in {"tags", "ai_metadata"} else f"{key} = %s")
                            params.append(json.dumps(data[key], ensure_ascii=False) if key in {"tags", "ai_metadata"} else data[key])
                    if not sets:
                        return self.get_item("image", item_id) or {}
                    params.append(int(item_id))
                    cur.execute(f"UPDATE image_library SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING id", tuple(params))
                    row = cur.fetchone()
                else:
                    cur.execute(
                        """
                        INSERT INTO image_library
                            (name, file_name, source, source_url, data_base64, mime_type, file_size,
                             thumb_media_id, thumb_media_id_expires_at, enabled, description, tags, category, ai_metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, '', NULL, %s, %s, %s::jsonb, %s, %s::jsonb)
                        RETURNING id
                        """,
                        (
                            data["name"],
                            data["file_name"],
                            data["source"],
                            data["source_url"],
                            data["data_base64"],
                            data["mime_type"],
                            data["file_size"],
                            data["enabled"],
                            data["description"],
                            json.dumps(data["tags"], ensure_ascii=False),
                            data["category"],
                            json.dumps(data["ai_metadata"], ensure_ascii=False),
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        self._ensure_image_variants(cur, {"id": row["id"], **data})
                conn.commit()
        if not row:
            raise NotFoundError("image item not found")
        return self.get_item("image", str(row["id"])) or {}

    def _save_miniprogram(self, payload: dict[str, Any], item_id: str | None) -> dict[str, Any]:
        pagepath = payload.get("pagepath") if payload.get("pagepath") is not None else payload.get("page_path")
        data = {
            "name": str(payload.get("name") or payload.get("title") or "").strip()[:200],
            "appid": str(payload.get("appid") or payload.get("app_id") or "").strip()[:120],
            "pagepath": str(pagepath or "").strip()[:500],
            "title": str(payload.get("title") or payload.get("name") or "").strip()[:200],
            "thumb_image_id": _coerce_optional_int(payload.get("thumb_image_id"), "thumb_image_id"),
            "thumb_media_id": str(payload.get("thumb_media_id") or "").strip()[:255],
            "enabled": _bool(payload.get("enabled", True)),
        }
        if not item_id:
            missing = [label for label, value in (("appid", data["appid"]), ("pagepath", data["pagepath"]), ("title", data["title"])) if not value]
            if missing:
                raise ContractError("小程序素材缺少必填字段：" + ", ".join(missing))
        else:
            for label in ("appid", "pagepath", "title"):
                if label in payload or (label == "pagepath" and "page_path" in payload):
                    if not data[label]:
                        raise ContractError("小程序素材字段不能为空：" + label)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    if data["thumb_image_id"] is not None:
                        cur.execute("SELECT 1 FROM image_library WHERE id = %s", (data["thumb_image_id"],))
                        if not cur.fetchone():
                            raise ContractError("thumb_image_id 对应的图片素材不存在")
                    if item_id:
                        sets = []
                        params = []
                        for key in ["name", "appid", "pagepath", "title", "thumb_image_id", "thumb_media_id", "enabled"]:
                            if key in payload or (key == "pagepath" and "page_path" in payload):
                                sets.append(f"{key} = %s")
                                params.append(str(payload.get("thumb_media_id") or "") if key == "thumb_media_id" else data[key])
                        if "thumb_image_id" in payload:
                            sets.append("thumb_media_id = ''")
                            sets.append("thumb_media_id_expires_at = NULL")
                        if not sets:
                            return self.get_item("miniprogram", item_id) or {}
                        params.append(int(item_id))
                        cur.execute(f"UPDATE miniprogram_library SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING id", tuple(params))
                        row = cur.fetchone()
                    else:
                        cur.execute(
                            """
                            INSERT INTO miniprogram_library
                                (name, appid, pagepath, title, thumb_image_url, thumb_image_base64,
                                 thumb_image_id, thumb_media_id, thumb_media_id_expires_at, enabled)
                            VALUES (%s, %s, %s, %s, '', '', %s, %s, NULL, %s)
                            RETURNING id
                            """,
                            (data["name"], data["appid"], data["pagepath"], data["title"], data["thumb_image_id"], data["thumb_media_id"], data["enabled"]),
                        )
                        row = cur.fetchone()
                    conn.commit()
        except (ContractError, NotFoundError):
            raise
        except Exception as exc:
            safe_log_exception(
                logger,
                "miniprogram library save failed",
                exc,
                item_id=item_id,
                payload_keys=sorted(payload.keys()),
            )
            raise ContractError("小程序素材保存失败：数据库写入异常，请稍后重试") from exc
        if not row:
            raise NotFoundError("miniprogram item not found")
        item = self.get_item("miniprogram", str(row["id"])) or {}
        required = {"id", "name", "appid", "pagepath", "page_path", "title", "thumb_image_id", "thumb_media_id", "enabled", "created_at", "updated_at"}
        missing_keys = sorted(required - set(item))
        if missing_keys:
            logger.error("miniprogram library saved item missing fields: %s", missing_keys)
            raise ContractError("小程序素材保存失败：返回字段不完整")
        return item

    def _save_attachment(self, payload: dict[str, Any], item_id: str | None) -> dict[str, Any]:
        data = {
            "name": str(payload.get("name") or payload.get("file_name") or "附件素材").strip()[:200],
            "file_name": str(payload.get("file_name") or "attachment.bin").strip()[:200],
            "mime_type": str(payload.get("mime_type") or "application/octet-stream").strip()[:120],
            "file_size": int(payload.get("file_size") or 0),
            "data_base64": str(payload.get("data_base64") or ""),
            "tags": normalize_tags(payload.get("tags")),
            "enabled": _bool(payload.get("enabled", True)),
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                if item_id:
                    sets = []
                    params = []
                    for key in ["name", "tags", "enabled"]:
                        if key in payload:
                            sets.append("tags = %s::jsonb" if key == "tags" else f"{key} = %s")
                            params.append(json.dumps(data[key], ensure_ascii=False) if key == "tags" else data[key])
                    if not sets:
                        return self.get_item("attachment", item_id) or {}
                    params.append(int(item_id))
                    cur.execute(f"UPDATE attachment_library SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING id", tuple(params))
                    row = cur.fetchone()
                else:
                    cur.execute(
                        """
                        INSERT INTO attachment_library
                            (name, file_name, mime_type, file_size, data_base64, media_id,
                             media_id_expires_at, enabled, description, tags)
                        VALUES (%s, %s, %s, %s, %s, '', NULL, %s, '', %s::jsonb)
                        RETURNING id
                        """,
                        (
                            data["name"],
                            data["file_name"],
                            data["mime_type"],
                            data["file_size"],
                            data["data_base64"],
                            data["enabled"],
                            json.dumps(data["tags"], ensure_ascii=False),
                        ),
                    )
                    row = cur.fetchone()
                conn.commit()
        if not row:
            raise NotFoundError("attachment item not found")
        return self.get_item("attachment", str(row["id"])) or {}

    def _save_group_invite(self, payload: dict[str, Any], item_id: str | None) -> dict[str, Any]:
        if item_id and "join_url" in payload:
            payload = {**payload, "binding_status": "ready"}
        title = str(payload.get("title") or payload.get("name") or "").strip()
        description = str(payload.get("description") or "").strip()
        join_url = normalize_group_invite_join_url(payload.get("join_url")) if "join_url" in payload or not item_id else ""
        pic_url = normalize_http_url(payload.get("pic_url"), field_name="卡片封面链接") if "pic_url" in payload else ""
        if not item_id and (not title or not join_url):
            raise ContractError("群邀请卡片缺少必填字段：title, join_url")
        if len(title.encode("utf-8")) > 128 or len(description.encode("utf-8")) > 512:
            raise ContractError("群邀请卡片标题或描述超过企微长度限制")
        data = {
            "name": str(payload.get("name") or title).strip()[:200],
            "title": title,
            "description": description,
            "pic_url": pic_url,
            "join_url": join_url,
            "config_id": str(payload.get("config_id") or "").strip()[:255],
            "state": str(payload.get("state") or "").strip()[:255],
            "chat_id_list": [str(value).strip() for value in list(payload.get("chat_id_list") or []) if str(value).strip()],
            "chat_id": str(payload.get("chat_id") or ((payload.get("chat_id_list") or [""])[0])).strip(),
            "binding_status": str(payload.get("binding_status") or ("ready" if join_url else "pending")).strip().lower(),
            "auto_create_room": _bool(payload.get("auto_create_room", True)),
            "room_base_name": str(payload.get("room_base_name") or "").strip()[:200],
            "room_base_id": _coerce_optional_int(payload.get("room_base_id"), "room_base_id"),
            "enabled": _bool(payload.get("enabled", True)),
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                if item_id:
                    sets: list[str] = []
                    params: list[Any] = []
                    for key in [
                        "name", "title", "description", "pic_url", "join_url", "config_id", "state",
                        "chat_id_list", "chat_id", "binding_status", "auto_create_room", "room_base_name", "room_base_id", "enabled",
                    ]:
                        if key not in payload:
                            continue
                        if key == "title" and not data[key]:
                            raise ContractError("群邀请卡片标题不能为空")
                        if key == "join_url" and not data[key]:
                            raise ContractError("群邀请链接不能为空")
                        if key == "join_url":
                            data["binding_status"] = "ready"
                        sets.append("chat_id_list = %s::jsonb" if key == "chat_id_list" else f"{key} = %s")
                        params.append(json.dumps(data[key], ensure_ascii=False) if key == "chat_id_list" else data[key])
                    if not sets:
                        return self.get_item("group_invite", item_id) or {}
                    params.append(int(item_id))
                    cur.execute(
                        f"UPDATE group_invite_library SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING id",
                        tuple(params),
                    )
                    row = cur.fetchone()
                else:
                    cur.execute(
                        """
                        INSERT INTO group_invite_library
                            (name, title, description, pic_url, join_url, config_id, state,
                             chat_id_list, chat_id, binding_status, auto_create_room, room_base_name, room_base_id, enabled)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            data["name"], data["title"], data["description"], data["pic_url"], data["join_url"],
                            data["config_id"], data["state"], json.dumps(data["chat_id_list"], ensure_ascii=False),
                            data["chat_id"], data["binding_status"], data["auto_create_room"], data["room_base_name"], data["room_base_id"], data["enabled"],
                        ),
                    )
                    row = cur.fetchone()
                conn.commit()
        if not row:
            raise NotFoundError("group_invite item not found")
        return self.get_item("group_invite", str(row["id"])) or {}

    def ensure_group_invite_binding(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = str(payload.get("chat_id") or "").strip()
        if not chat_id:
            raise ContractError("chat_id 不能为空")
        group_name = str(payload.get("group_name") or chat_id).strip()
        title = f"加入「{group_name}」"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_invite_library
                        (name, title, description, join_url, chat_id_list, chat_id,
                         binding_status, auto_create_room, room_base_name, enabled)
                    VALUES (%s, %s, %s, '', %s::jsonb, %s, 'pending', FALSE, %s, TRUE)
                    ON CONFLICT (chat_id) WHERE chat_id <> ''
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        title = EXCLUDED.title,
                        room_base_name = EXCLUDED.room_base_name,
                        chat_id_list = EXCLUDED.chat_id_list,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                    """,
                    (group_name, title, "点击卡片直接加入群聊", json.dumps([chat_id], ensure_ascii=False), chat_id, group_name),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ContractError("群邀请绑定创建失败")
        return self.get_item("group_invite", str(row["id"])) or {}

    def claim_group_invite_provisioning(self, item_id: str, claim_token: str, state: str) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_invite_library
                    SET config_id = %s, state = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND COALESCE(binding_status, 'pending') <> 'invalid'
                      AND COALESCE(join_url, '') = ''
                      AND (
                        COALESCE(config_id, '') = ''
                        OR (COALESCE(config_id, '') LIKE 'provisioning:%%' AND updated_at < CURRENT_TIMESTAMP - INTERVAL '2 minutes')
                      )
                    RETURNING id
                    """,
                    (claim_token, state, int(item_id)),
                )
                claimed = cur.fetchone()
            conn.commit()
        item = self.get_item("group_invite", item_id)
        if not item:
            raise NotFoundError("group_invite item not found")
        return {"claimed": bool(claimed), "item": item}

    def store_group_invite_config(self, item_id: str, claim_token: str, config_id: str, state: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_invite_library
                    SET config_id = %s, state = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND config_id = %s AND COALESCE(join_url, '') = ''
                    RETURNING id
                    """,
                    (config_id, state, int(item_id), claim_token),
                )
                row = cur.fetchone()
            conn.commit()
        return self.get_item("group_invite", str(row["id"])) if row else None

    def release_group_invite_provisioning(self, item_id: str, claim_token: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_invite_library
                    SET config_id = '', updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND config_id = %s AND COALESCE(join_url, '') = ''
                    """,
                    (int(item_id), claim_token),
                )
            conn.commit()

    def complete_group_invite_provisioning(self, item_id: str, config_id: str, state: str, join_url: str) -> dict[str, Any]:
        normalized_url = normalize_group_invite_join_url(join_url)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE group_invite_library
                    SET config_id = %s,
                        state = %s,
                        join_url = %s,
                        binding_status = 'ready',
                        enabled = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND config_id = %s AND COALESCE(binding_status, 'pending') <> 'invalid'
                    RETURNING id
                    """,
                    (config_id, state, normalized_url, int(item_id), config_id),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ContractError("群邀请自动生成状态已变化，请重试")
        return self.get_item("group_invite", str(row["id"])) or {}

    def reconcile_group_invite_bindings(self, active_chat_ids: list[str], inactive_chat_ids: list[str]) -> int:
        active = [str(value or "").strip() for value in active_chat_ids if str(value or "").strip()]
        inactive = [str(value or "").strip() for value in inactive_chat_ids if str(value or "").strip()]
        changed = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                if inactive:
                    cur.execute(
                        """
                        UPDATE group_invite_library
                        SET binding_status = 'invalid', updated_at = CURRENT_TIMESTAMP
                        WHERE chat_id = ANY(%s) AND binding_status <> 'invalid'
                        """,
                        (inactive,),
                    )
                    changed += int(cur.rowcount or 0)
                if active:
                    cur.execute(
                        """
                        UPDATE group_invite_library
                        SET binding_status = CASE WHEN join_url = '' THEN 'pending' ELSE 'ready' END,
                            enabled = TRUE,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE chat_id = ANY(%s) AND binding_status = 'invalid'
                        """,
                        (active,),
                    )
                    changed += int(cur.rowcount or 0)
            conn.commit()
        return changed

    def _delete_image(self, item_id: str, *, force: bool) -> dict[str, Any]:
        refs = self._image_references(item_id)
        if (refs["miniprograms"] or refs["campaign_steps"]) and not force:
            return {"ok": False, "error": "image_has_references", "references": refs}
        cleared = {"miniprograms_cleared": 0, "campaign_steps_cleared": 0}
        with self._connect() as conn:
            with conn.cursor() as cur:
                if force:
                    cur.execute("UPDATE miniprogram_library SET thumb_image_id = NULL, thumb_media_id = '', thumb_media_id_expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE thumb_image_id = %s", (int(item_id),))
                    cleared["miniprograms_cleared"] = int(cur.rowcount or 0)
                    cur.execute(
                        """
                        UPDATE campaign_steps
                        SET content_payload_json = jsonb_set(
                            COALESCE(content_payload_json, '{}'::jsonb),
                            '{image_library_ids}',
                            COALESCE((
                                SELECT jsonb_agg(elem)
                                FROM jsonb_array_elements(COALESCE(content_payload_json->'image_library_ids', '[]'::jsonb)) AS elem
                                WHERE trim(both '"' from elem::text) <> %s
                            ), '[]'::jsonb),
                            true
                        ),
                        updated_at = CURRENT_TIMESTAMP
                        WHERE EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements_text(COALESCE(content_payload_json->'image_library_ids', '[]'::jsonb)) AS iid
                            WHERE iid = %s
                        )
                        """,
                        (str(item_id), str(item_id)),
                    )
                    cleared["campaign_steps_cleared"] = int(cur.rowcount or 0)
                cur.execute("DELETE FROM image_library WHERE id = %s", (int(item_id),))
                deleted = int(cur.rowcount or 0)
            conn.commit()
        if not deleted:
            raise NotFoundError("image item not found")
        return {"ok": True, "deleted": True, "hard_deleted": True, "id": int(item_id), "references_cleared": cleared}

    def _image_references(self, item_id: str) -> dict[str, list[dict[str, Any]]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, COALESCE(NULLIF(title, ''), name) AS title FROM miniprogram_library WHERE thumb_image_id = %s ORDER BY id", (int(item_id),))
                miniprograms = [dict(row) for row in cur.fetchall() or []]
                cur.execute(
                    """
                    SELECT id, campaign_id, campaign_segment_id, step_index
                    FROM campaign_steps
                    WHERE EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements_text(COALESCE(content_payload_json->'image_library_ids', '[]'::jsonb)) AS iid
                        WHERE iid = %s
                    )
                    ORDER BY id
                    """,
                    (str(item_id),),
                )
                campaign_steps = [dict(row) for row in cur.fetchall() or []]
        return {"miniprograms": miniprograms, "campaign_steps": campaign_steps}

    def _serialize(self, kind: str, row: dict[str, Any], *, include_data: bool, use_thumbnail_fallback: bool = False) -> dict[str, Any]:
        if kind == "image":
            item = {
                "id": int(row.get("id") or 0),
                "name": str(row.get("name") or ""),
                "file_name": str(row.get("file_name") or ""),
                "source": str(row.get("source") or "upload"),
                "source_url": str(row.get("source_url") or ""),
                "mime_type": str(row.get("mime_type") or "image/png"),
                "content_type": str(row.get("mime_type") or "image/png"),
                "file_size": int(row.get("file_size") or 0),
                "thumb_media_id": str(row.get("thumb_media_id") or ""),
                "thumb_media_id_expires_at": _iso(row.get("thumb_media_id_expires_at")),
                "enabled": _bool(row.get("enabled")),
                "description": str(row.get("description") or ""),
                "tags": normalize_tags(_json(row.get("tags"), [])),
                "category": str(row.get("category") or ""),
                "ai_metadata": _json(row.get("ai_metadata"), {}),
                "width": int(row.get("width") or 0),
                "height": int(row.get("height") or 0),
                "created_at": _iso(row.get("created_at")),
                "updated_at": _iso(row.get("updated_at")),
            }
            add_image_variant_urls(item, use_thumbnail_fallback=use_thumbnail_fallback)
            if include_data:
                item["data_base64"] = str(row.get("data_base64") or "")
                item["data_url"] = f"data:{item['mime_type']};base64,{item['data_base64']}"
            return item
        if kind == "miniprogram":
            pagepath = str(row.get("pagepath") or "")
            item = {
                "id": int(row.get("id") or 0),
                "name": str(row.get("name") or ""),
                "appid": str(row.get("appid") or ""),
                "pagepath": pagepath,
                "page_path": pagepath,
                "title": str(row.get("title") or ""),
                "thumb_image_id": row.get("thumb_image_id"),
                "thumb_media_id": str(row.get("thumb_media_id") or ""),
                "thumb_media_id_expires_at": _iso(row.get("thumb_media_id_expires_at")),
                "thumb_image_url": str(row.get("thumb_image_url") or ""),
                "thumb_image_base64": str(row.get("thumb_image_base64") or ""),
                "enabled": _bool(row.get("enabled")),
                "created_at": _iso(row.get("created_at")),
                "updated_at": _iso(row.get("updated_at")),
            }
            thumb_image_id = item.get("thumb_image_id")
            if thumb_image_id not in (None, ""):
                add_image_variant_urls(item, thumb_image_id, use_thumbnail_fallback=use_thumbnail_fallback)
            return item
        if kind == "group_invite":
            return {
                "id": int(row.get("id") or 0),
                "name": str(row.get("name") or ""),
                "title": str(row.get("title") or ""),
                "description": str(row.get("description") or ""),
                "pic_url": str(row.get("pic_url") or ""),
                "join_url": str(row.get("join_url") or ""),
                "config_id": str(row.get("config_id") or ""),
                "state": str(row.get("state") or ""),
                "chat_id_list": [str(value) for value in _json(row.get("chat_id_list"), [])],
                "chat_id": str(row.get("chat_id") or ((_json(row.get("chat_id_list"), []) or [""])[0]) or ""),
                "binding_status": str(row.get("binding_status") or ("ready" if row.get("join_url") else "pending")),
                "auto_create_room": _bool(row.get("auto_create_room")),
                "room_base_name": str(row.get("room_base_name") or ""),
                "room_base_id": row.get("room_base_id"),
                "enabled": _bool(row.get("enabled")),
                "created_at": _iso(row.get("created_at")),
                "updated_at": _iso(row.get("updated_at")),
            }
        item = {
            "id": int(row.get("id") or 0),
            "name": str(row.get("name") or ""),
            "file_name": str(row.get("file_name") or ""),
            "mime_type": str(row.get("mime_type") or "application/octet-stream"),
            "file_size": int(row.get("file_size") or 0),
            "media_id": str(row.get("media_id") or ""),
            "media_id_expires_at": _iso(row.get("media_id_expires_at")),
            "tags": normalize_tags(_json(row.get("tags"), [])),
            "enabled": _bool(row.get("enabled")),
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("updated_at")),
        }
        if include_data:
            item["data_base64"] = str(row.get("data_base64") or "")
        return item

    def _table(self, kind: str) -> str:
        if kind == "image":
            return "image_library"
        if kind == "miniprogram":
            return "miniprogram_library"
        if kind == "group_invite":
            return "group_invite_library"
        return "attachment_library"
