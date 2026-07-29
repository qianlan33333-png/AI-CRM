from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


ALLOWED_OPERATION_MEMBER_SCOPES = {
    "group_ops",
    "automation",
    "channel_code",
    "ai_assistant",
    "common",
    "wecom_directory",
}


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_scope(value: str) -> str:
    scope = clean_text(value) or "common"
    return scope if scope in ALLOWED_OPERATION_MEMBER_SCOPES else "common"


def clamp_page(value: Any) -> int:
    try:
        page = int(value or 1)
    except (TypeError, ValueError):
        page = 1
    return max(1, page)


def clamp_page_size(value: Any) -> int:
    try:
        page_size = int(value or 30)
    except (TypeError, ValueError):
        page_size = 30
    return max(1, min(page_size, 100))


def bool_from_query(value: Any) -> bool:
    normalized = clean_text(value).lower()
    return normalized in {"1", "true", "yes", "on"}


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_raw_value(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@dataclass
class OperationMemberCandidate:
    user_id: str
    display_name: str = ""
    avatar_url: str = ""
    department_name: str = ""
    status: str = "active"
    source: str = "unknown"
    extra: dict[str, Any] = field(default_factory=dict)
    search_blob: str = ""
    priority: int = 100

    def display_score(self) -> int:
        name = clean_text(self.display_name)
        if not name or name == self.user_id:
            return 0
        return len(name)

    def sort_key(self) -> tuple[int, int, int, str]:
        return (-self.display_score(), 0 if self.avatar_url else 1, self.priority, self.user_id.lower())

    def search_text(self) -> str:
        return " ".join(
            [
                self.user_id,
                self.display_name,
                self.department_name,
                self.status,
                self.source,
                self.search_blob,
                json.dumps(self.extra, ensure_ascii=False, sort_keys=True),
            ]
        ).lower()

    def to_item(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name or self.user_id,
            "avatar_url": self.avatar_url,
            "department_name": self.department_name,
            "status": self.status,
            "source": self.source,
            "extra": self.extra or {},
        }


def candidate_from_row(row: dict[str, Any], *, fallback_source: str = "unknown", priority: int = 100) -> OperationMemberCandidate | None:
    raw = _json_obj(row.get("raw_payload_json") or row.get("raw_profile") or row.get("raw_follow_user") or row.get("raw_payload"))
    user_id = clean_text(row.get("user_id") or row.get("userid") or row.get("wecom_userid") or row.get("owner_staff_id"))
    if not user_id:
        return None
    display_name = clean_text(row.get("display_name") or row.get("name") or raw.get("name") or raw.get("userid")) or user_id
    avatar_url = clean_text(row.get("avatar_url") or row.get("avatar") or _first_raw_value(raw, ("avatar", "thumb_avatar", "avatar_url")))
    department_name = clean_text(
        row.get("department_name")
        or row.get("department")
        or raw.get("department_name")
        or raw.get("main_department")
        or raw.get("department")
    )
    active_value = row.get("is_active")
    row_status = clean_text(row.get("status") or row.get("relation_status"))
    if active_value is not None:
        is_active = str(active_value).lower() not in {"0", "false", "f", "no"}
        status = "active" if is_active else "inactive"
    elif row_status:
        status = "active" if row_status == "active" else row_status
    else:
        status = "active"
    extra = dict(row.get("extra") or {})
    for key in ("role", "position", "mobile", "wecom_status"):
        value = clean_text(row.get(key) or raw.get(key))
        if value:
            extra[key] = value
    search_blob = " ".join(
        [
            clean_text(row.get("search_blob")),
            clean_text(row.get("position")),
            clean_text(row.get("role")),
            clean_text(row.get("mobile")),
            json.dumps(raw, ensure_ascii=False, sort_keys=True) if raw else "",
        ]
    )
    return OperationMemberCandidate(
        user_id=user_id,
        display_name=display_name,
        avatar_url=avatar_url,
        department_name=department_name,
        status=status,
        source=clean_text(row.get("source")) or fallback_source,
        extra=extra,
        search_blob=search_blob,
        priority=int(row.get("priority") or priority),
    )


def operation_members_payload(
    rows: list[dict[str, Any]],
    *,
    q: str = "",
    scope: str = "common",
    page: int = 1,
    page_size: int = 30,
    include_inactive: bool = False,
) -> dict[str, Any]:
    # CRM unified operation-member selector: business pages save their own owner fields;
    # this service only normalizes searchable member candidates.
    del scope
    normalized_q = clean_text(q).lower()
    normalized_page = clamp_page(page)
    normalized_page_size = clamp_page_size(page_size)
    deduped: dict[str, OperationMemberCandidate] = {}
    for row in rows:
        candidate = candidate_from_row(row)
        if candidate is None:
            continue
        if not include_inactive and candidate.status != "active":
            continue
        if normalized_q and normalized_q not in candidate.search_text():
            continue
        current = deduped.get(candidate.user_id)
        if current is None or candidate.sort_key() < current.sort_key():
            deduped[candidate.user_id] = candidate
    items = sorted(deduped.values(), key=lambda item: item.sort_key())
    total = len(items)
    offset = (normalized_page - 1) * normalized_page_size
    page_items = items[offset : offset + normalized_page_size]
    return {
        "items": [item.to_item() for item in page_items],
        "total": total,
        "page": normalized_page,
        "page_size": normalized_page_size,
    }
