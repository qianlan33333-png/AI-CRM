from __future__ import annotations

from typing import Any, Iterable, Protocol

from aicrm_next.platform.shared.postgres_connection import db_session
from aicrm_next.platform.shared.wecom_payload_contract import (
    normalize_miniprogram_attachment_payload,
)


class DynamicCardMediaPort(Protocol):
    def validate_cover_ids(self, cover_image_ids: Iterable[int]) -> dict[int, str]: ...

    def resolve_attachment(self, card: dict[str, Any]) -> dict[str, Any]: ...


class PostgresDynamicCardMediaPort:
    """Shared-cover adapter for personalized mini-program cards."""

    def validate_cover_ids(self, cover_image_ids: Iterable[int]) -> dict[int, str]:
        ids = sorted({int(value) for value in cover_image_ids if int(value or 0) > 0})
        if not ids:
            return {}
        with db_session() as connection:
            rows = connection.execute(
                """
                SELECT id, enabled,
                       (COALESCE(data_base64, '') <> '' OR COALESCE(source_url, '') <> '') AS source_ready
                FROM image_library
                WHERE id = ANY(?)
                """,
                (ids,),
            ).fetchall()
        found = {int(row["id"]): row for row in rows}
        result: dict[int, str] = {}
        for image_id in ids:
            item = found.get(image_id)
            if not item:
                result[image_id] = "cover_image_not_found"
            elif not bool(item.get("enabled")):
                result[image_id] = "cover_image_disabled"
            elif not bool(item.get("source_ready")):
                result[image_id] = "cover_image_source_missing"
            else:
                result[image_id] = ""
        return result

    def resolve_attachment(self, card: dict[str, Any]) -> dict[str, Any]:
        from .wecom_lease import build_wecom_media_lease_manager

        cover_image_id = int(card.get("cover_image_id") or 0)
        lease = build_wecom_media_lease_manager().ensure_ready(
            "image",
            cover_image_id,
            upload_kind="image",
        )
        payload = normalize_miniprogram_attachment_payload(
            {
                "appid": str(card.get("appid") or "").strip(),
                "title": str(card.get("title") or "").strip(),
                "page": str(card.get("pagepath") or "").strip(),
                "pic_media_id": str(lease.get("media_id") or "").strip(),
            }
        )
        return {"msgtype": "miniprogram", "miniprogram": payload}


def build_dynamic_card_media_port() -> DynamicCardMediaPort:
    return PostgresDynamicCardMediaPort()


__all__ = [
    "DynamicCardMediaPort",
    "PostgresDynamicCardMediaPort",
    "build_dynamic_card_media_port",
]
