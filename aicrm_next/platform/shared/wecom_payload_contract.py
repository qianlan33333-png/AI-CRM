from __future__ import annotations

import json
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_group_admin_userids(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        value = [value] if _text(value) else []
    result: list[str] = []
    for item in list(value or []):
        userid = _text(item.get("userid") if isinstance(item, dict) else item)
        if userid and userid not in result:
            result.append(userid)
    return result


def normalize_miniprogram_attachment_payload(attachment_payload: dict[str, Any]) -> dict[str, str]:
    appid = _text(attachment_payload.get("appid"))
    page = _text(attachment_payload.get("page") or attachment_payload.get("pagepath"))
    title = _text(attachment_payload.get("title"))
    pic_media_id = _text(attachment_payload.get("pic_media_id") or attachment_payload.get("thumb_media_id"))
    if not appid:
        raise ValueError("miniprogram attachments must include appid")
    if not page:
        raise ValueError("miniprogram attachments must include page")
    if not title:
        raise ValueError("miniprogram attachments must include title")
    if not pic_media_id:
        raise ValueError("miniprogram attachments must include pic_media_id")
    return {
        "appid": appid,
        "page": page,
        "title": title,
        "pic_media_id": pic_media_id,
    }
