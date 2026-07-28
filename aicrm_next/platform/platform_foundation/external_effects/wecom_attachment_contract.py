from __future__ import annotations

from typing import Any


def wecom_provider_attachment_ready(item: Any) -> bool:
    if not isinstance(item, dict) or item.get("material_id") not in (None, "") or "dependency_key" in str(item):
        return False
    msgtype = str(item.get("msgtype") or "").strip()
    nested = item.get(msgtype) if isinstance(item.get(msgtype), dict) else {}
    if msgtype in {"image", "file"}:
        return bool(str(nested.get("media_id") or "").strip())
    if msgtype == "miniprogram":
        return all(
            str(value or "").strip()
            for value in (
                nested.get("appid"),
                nested.get("page") or nested.get("pagepath"),
                nested.get("title"),
                nested.get("pic_media_id") or nested.get("thumb_media_id"),
            )
        )
    if msgtype == "link":
        return bool(str(nested.get("title") or "").strip() and str(nested.get("url") or "").strip())
    return False


__all__ = ["wecom_provider_attachment_ready"]
