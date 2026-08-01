from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from aicrm_next.engagement.send_content.application import PreviewSendContentPackageQuery
from aicrm_next.engagement.send_content.dto import SendContentPreviewRequest
from aicrm_next.platform.shared.sensitive_data import redact_sensitive_text

from .repository import AudienceSendRecordRepository, build_audience_send_record_repository


_SOURCE_LABELS = {
    "agent_bot": "Agent 机器人",
    "fixed_script": "固定话术",
    "manual_broadcast": "手动群发",
}

_STATUS_LABELS = {
    "queued": "排队中",
    "sending": "发送中",
    "retrying": "重试中",
    "sent": "发送成功",
    "failed": "发送失败",
    "cancelled": "已取消",
    "unknown_after_dispatch": "调用后状态未知",
    "simulated": "模拟执行",
}

_PLANNED_STATUSES = {"pending", "planned", "approved", "waiting_approval", "queued", "claimed", "delegated", "created"}
_SENDING_STATUSES = {"sending", "dispatching"}
_FAILED_STATUSES = {"failed", "failed_terminal", "blocked", "expired", "skipped"}
_CANCELLED_STATUSES = {"cancelled"}
_SIMULATED_STATUSES = {"simulated", "execute_dryrun", "shadow", "plan_only"}


class ListAudienceSendRecordsQuery:
    def __init__(self, repo: AudienceSendRecordRepository | None = None) -> None:
        self._repo = repo

    def execute(self, package_id: int, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        repository = self._repo or build_audience_send_record_repository()
        package = repository.get_package(int(package_id))
        if package is None:
            return {"ok": False, "error": "package_not_found"}
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset or 0))
        rows, total = repository.list_records(
            package_id=int(package_id),
            package_key=str(package.get("package_key") or ""),
            limit=safe_limit,
            offset=safe_offset,
        )
        items = [_list_item(row) for row in rows]
        return {
            "ok": True,
            "items": items,
            "total": int(total),
            "limit": safe_limit,
            "offset": safe_offset,
        }

    __call__ = execute


class GetAudienceSendRecordQuery:
    def __init__(
        self,
        repo: AudienceSendRecordRepository | None = None,
        *,
        material_previewer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._repo = repo
        self._material_previewer = material_previewer

    def execute(self, package_id: int, record_id: str) -> dict[str, Any]:
        repository = self._repo or build_audience_send_record_repository()
        package = repository.get_package(int(package_id))
        if package is None:
            return {"ok": False, "error": "package_not_found"}
        normalized_record_id = str(record_id or "").strip()
        if not _valid_record_id(normalized_record_id):
            return {"ok": False, "error": "send_record_not_found"}
        row = repository.get_record(
            package_id=int(package_id),
            package_key=str(package.get("package_key") or ""),
            record_id=normalized_record_id,
        )
        if row is None:
            return {"ok": False, "error": "send_record_not_found"}

        item = _list_item(row)
        content_text, content_basis = _content_fact(row)
        attachments, attachment_basis = self._attachments(row)
        item.update(
            {
                "content_text": content_text,
                "content_basis": content_basis,
                "content_basis_label": {
                    "frozen_effect_payload": "发送任务冻结内容",
                    "message_snapshot": "消息快照",
                    "planned_snapshot": "计划内容",
                }[content_basis],
                "has_attachments": bool(attachments),
                "attachment_basis": attachment_basis,
                "attachment_basis_label": {
                    "materialized": "实际发送附件",
                    "planned": "计划携带附件",
                    "none": "未携带附件",
                }[attachment_basis],
                "attachment_count": len(attachments),
                "attachments": attachments,
                "technical_attempt_count": max(0, int(row.get("attempt_count") or 0)),
                "technical_retry_count": max(0, int(row.get("attempt_count") or 0) - 1),
            }
        )
        return {"ok": True, "record": item}

    def _attachments(self, row: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        actual = _json_list(row.get("actual_attachments"))
        if actual:
            frozen = [_sanitize_materialized_attachment(item) for item in actual if isinstance(item, dict)]
            return self._enrich_materialized_attachments(row, frozen), "materialized"

        planned_inline = _json_list(row.get("planned_attachments"))
        if planned_inline:
            return [_sanitize_materialized_attachment(item, planned=True) for item in planned_inline if isinstance(item, dict)], "planned"

        media_refs = _json_list(row.get("media_refs"))
        content_package, unresolved_media_refs = _content_package_with_media_refs(_content_package(row), media_refs)
        if not _content_package_has_materials(content_package) and not media_refs:
            return [], "none"

        preview = self._preview_content_package(content_package)
        materials = list(dict(preview.get("preview") or {}).get("materials") or []) if preview else []
        if materials:
            sanitized = [_sanitize_preview_material(item) for item in materials if isinstance(item, dict)]
            return _merge_missing_planned_materials(sanitized, content_package, unresolved_media_refs), "planned"
        return _fallback_planned_materials(content_package, unresolved_media_refs), "planned"

    def _enrich_materialized_attachments(
        self,
        row: dict[str, Any],
        frozen: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Use the plan only for labels/previews; frozen attachment type/count stay authoritative."""

        media_refs = _json_list(row.get("media_refs"))
        content_package, _ = _content_package_with_media_refs(_content_package(row), media_refs)
        preview = self._preview_content_package(content_package)
        planned = []
        if preview:
            planned = [
                _sanitize_preview_material(item)
                for item in list(dict(preview.get("preview") or {}).get("materials") or [])
                if isinstance(item, dict)
            ]
        remaining = list(planned)
        enriched: list[dict[str, Any]] = []
        generic_names = {"图片", "小程序", "文件 / PDF", "客户群邀请", "附件"}
        for frozen_item in frozen:
            kind = str(frozen_item.get("type") or "attachment")
            match_index = next(
                (index for index, candidate in enumerate(remaining) if str(candidate.get("type") or "attachment") == kind),
                None,
            )
            match = remaining.pop(match_index) if match_index is not None else {}
            current = dict(frozen_item)
            if str(current.get("name") or "") in generic_names and match.get("name"):
                current["name"] = match["name"]
            if not current.get("description") and match.get("description"):
                current["description"] = match["description"]
            if not current.get("thumbnail_url") and match.get("thumbnail_url"):
                current["thumbnail_url"] = match["thumbnail_url"]
            enriched.append(current)
        return enriched

    def _preview_content_package(self, content_package: dict[str, Any]) -> dict[str, Any]:
        if not _content_package_has_materials(content_package):
            return {}
        try:
            if self._material_previewer is not None:
                return self._material_previewer(content_package)
            return PreviewSendContentPackageQuery()(
                SendContentPreviewRequest(
                    content_package=content_package,
                    text_enabled=True,
                    require_body=False,
                )
            )
        except Exception:
            # A deleted library asset must not make the historical message unreadable.
            return {}

    __call__ = execute


def _valid_record_id(record_id: str) -> bool:
    prefix, separator, numeric = record_id.partition(":")
    return bool(separator and prefix in {"automation", "manual"} and numeric.isdigit() and int(numeric) > 0)


def _list_item(row: dict[str, Any]) -> dict[str, Any]:
    status = _status(row)
    send_time = _send_time(row, status)
    nickname = str(row.get("nickname") or "").strip() or "未命名客户"
    failure_reason = str(row.get("failure_reason") or "").strip()
    if failure_reason:
        failure_reason = redact_sensitive_text(failure_reason)
    has_attachments = bool(
        _json_list(row.get("actual_attachments"))
        or _json_list(row.get("planned_attachments"))
        or _json_list(row.get("media_refs"))
        or _content_package_has_materials(_content_package(row))
    )
    content_text, _ = _content_fact(row)
    return {
        "record_id": str(row.get("record_id") or ""),
        "nickname": nickname,
        "external_userid": str(row.get("external_userid") or "").strip(),
        "source": str(row.get("source") or ""),
        "source_label": _SOURCE_LABELS.get(str(row.get("source") or ""), "自动化话术"),
        "status": status,
        "status_label": _STATUS_LABELS[status],
        "send_time": send_time,
        "failure_reason": failure_reason,
        "has_attachments": has_attachments,
        "detail_available": bool(content_text or has_attachments),
    }


def _status(row: dict[str, Any]) -> str:
    raw = str(row.get("raw_status") or row.get("business_status") or "").strip().lower()
    side_effect_executed = bool(row.get("side_effect_executed"))
    provider_result_received = bool(row.get("provider_result_received"))
    provider_called = bool(row.get("provider_call_started_at")) or side_effect_executed
    if raw in _SIMULATED_STATUSES:
        return "simulated"
    if raw in {"succeeded", "sent"}:
        if side_effect_executed and provider_result_received:
            return "sent"
        return "unknown_after_dispatch"
    if raw == "unknown_after_dispatch":
        return "unknown_after_dispatch"
    if raw == "failed_retryable":
        return "retrying"
    if raw in _CANCELLED_STATUSES:
        return "cancelled"
    if raw in _FAILED_STATUSES:
        return "failed"
    if raw in _SENDING_STATUSES:
        return "sending"
    if raw in _PLANNED_STATUSES:
        return "sending" if provider_called else "queued"
    return "unknown_after_dispatch" if provider_called else "queued"


def _send_time(row: dict[str, Any], status: str) -> str | None:
    if status == "sent":
        return _public_datetime(row.get("message_sent_at") or row.get("effect_completed_at"))
    if status in {"failed", "retrying", "unknown_after_dispatch"} and row.get("provider_call_started_at"):
        return _public_datetime(row.get("provider_call_started_at"))
    return None


def _public_datetime(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _content_fact(row: dict[str, Any]) -> tuple[str, str]:
    actual_text = str(row.get("actual_content_text") or "")
    if bool(row.get("effect_materialized")) and actual_text:
        return actual_text, "frozen_effect_payload"
    message_text = str(row.get("message_content_text") or "")
    if message_text:
        return message_text, "message_snapshot"
    return str(row.get("planned_content_text") or ""), "planned_snapshot"


def _content_package(row: dict[str, Any]) -> dict[str, Any]:
    actual = _json_obj(row.get("actual_content_package"))
    planned = _json_obj(row.get("planned_content_package"))
    return {**planned, **actual}


def _content_package_with_media_refs(
    content_package: dict[str, Any],
    media_refs: list[Any],
) -> tuple[dict[str, Any], list[Any]]:
    merged = dict(content_package)
    field_by_kind = {
        "image": "image_library_ids",
        "miniprogram": "miniprogram_library_ids",
        "file": "attachment_library_ids",
        "attachment": "attachment_library_ids",
        "group_invite": "group_invite_library_ids",
        "link": "group_invite_library_ids",
    }
    for field in field_by_kind.values():
        normalized_ids: list[int] = []
        for raw_id in _json_list(merged.get(field)):
            library_id = _positive_library_id(raw_id)
            if library_id is not None and library_id not in normalized_ids:
                normalized_ids.append(library_id)
        merged[field] = normalized_ids
    unresolved: list[Any] = []
    for ref in media_refs:
        if not isinstance(ref, dict):
            continue
        field = field_by_kind.get(str(ref.get("kind") or ref.get("type") or "").strip().lower())
        library_id = _positive_library_id(ref.get("library_id"))
        if not field or library_id is None:
            unresolved.append(ref)
            continue
        if library_id not in merged[field]:
            merged[field].append(library_id)
    return merged, unresolved


def _positive_library_id(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _json_obj(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _content_package_has_materials(content_package: dict[str, Any]) -> bool:
    return any(
        content_package.get(key)
        for key in (
            "image_library_ids",
            "miniprogram_library_ids",
            "attachment_library_ids",
            "group_invite_library_ids",
            "dynamic_miniprogram_card",
        )
    )


def _sanitize_materialized_attachment(item: dict[str, Any], *, planned: bool = False) -> dict[str, Any]:
    kind = str(item.get("msgtype") or item.get("type") or item.get("kind") or "attachment").strip().lower()
    if kind == "file":
        kind = "attachment"
    nested = item.get(kind) if isinstance(item.get(kind), dict) else {}
    if kind == "attachment" and isinstance(item.get("file"), dict):
        nested = item["file"]
    title = str(nested.get("title") or item.get("title") or "").strip()
    if not title:
        title = {
            "image": "图片",
            "miniprogram": "小程序",
            "attachment": "文件 / PDF",
            "link": "客户群邀请",
            "group_invite": "客户群邀请",
        }.get(kind, "附件")
    return {
        "type": "group_invite" if kind == "link" else kind,
        "type_label": {
            "image": "图片",
            "miniprogram": "小程序",
            "attachment": "文件 / PDF",
            "link": "客户群邀请",
            "group_invite": "客户群邀请",
        }.get(kind, "附件"),
        "name": title,
        "description": str(nested.get("desc") or item.get("description") or "").strip(),
        "thumbnail_url": str(item.get("thumbnail_url") or "").strip(),
        "availability": "planned" if planned else "available",
    }


def _sanitize_preview_material(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("type") or item.get("material_type") or "attachment").strip().lower()
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "type": kind,
        "type_label": {
            "image": "图片",
            "miniprogram": "小程序",
            "attachment": "文件 / PDF",
            "group_invite": "客户群邀请",
        }.get(kind, "附件"),
        "name": str(item.get("name") or item.get("title") or metadata.get("title") or "未命名素材").strip(),
        "description": str(item.get("description") or metadata.get("description") or "").strip(),
        "thumbnail_url": str(item.get("thumbnail_url") or item.get("preview_url") or "").strip(),
        "availability": "available",
    }


def _fallback_planned_materials(content_package: dict[str, Any], media_refs: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    fields = (
        ("image_library_ids", "image", "图片"),
        ("miniprogram_library_ids", "miniprogram", "小程序"),
        ("attachment_library_ids", "attachment", "文件 / PDF"),
        ("group_invite_library_ids", "group_invite", "客户群邀请"),
    )
    for field, kind, label in fields:
        for library_id in _json_list(content_package.get(field)):
            items.append(
                {
                    "type": kind,
                    "type_label": label,
                    "name": f"{label}素材（已删除或不可用）",
                    "description": "素材快照不可用，仅保留计划引用",
                    "thumbnail_url": "",
                    "availability": "missing",
                }
            )
    if isinstance(content_package.get("dynamic_miniprogram_card"), dict):
        card = content_package["dynamic_miniprogram_card"]
        items.append(
            {
                "type": "miniprogram",
                "type_label": "小程序",
                "name": str(card.get("title") or "动态小程序卡片").strip(),
                "description": "计划携带",
                "thumbnail_url": "",
                "availability": "planned",
            }
        )
    for ref in media_refs:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or ref.get("type") or "attachment").strip().lower()
        label = {
            "image": "图片",
            "miniprogram": "小程序",
            "file": "文件 / PDF",
            "attachment": "文件 / PDF",
            "group_invite": "客户群邀请",
            "link": "客户群邀请",
        }.get(kind, "附件")
        items.append(
            {
                "type": "attachment" if kind == "file" else ("group_invite" if kind == "link" else kind),
                "type_label": label,
                "name": str(ref.get("name") or ref.get("title") or f"{label}（计划携带）").strip(),
                "description": "计划携带",
                "thumbnail_url": "",
                "availability": "planned",
            }
        )
    return items


def _merge_missing_planned_materials(
    resolved: list[dict[str, Any]],
    content_package: dict[str, Any],
    media_refs: list[Any],
) -> list[dict[str, Any]]:
    """Keep partial previews honest when one or more historical assets disappeared."""

    expected_by_type = {
        "image": len(_json_list(content_package.get("image_library_ids"))),
        "miniprogram": len(_json_list(content_package.get("miniprogram_library_ids"))),
        "attachment": len(_json_list(content_package.get("attachment_library_ids"))),
        "group_invite": len(_json_list(content_package.get("group_invite_library_ids"))),
    }
    if isinstance(content_package.get("dynamic_miniprogram_card"), dict):
        expected_by_type["miniprogram"] += 1
    resolved_by_type: dict[str, int] = {}
    for item in resolved:
        kind = str(item.get("type") or "attachment")
        resolved_by_type[kind] = resolved_by_type.get(kind, 0) + 1

    fallback = _fallback_planned_materials(content_package, [])
    missing_by_type = {
        kind: max(0, expected - resolved_by_type.get(kind, 0))
        for kind, expected in expected_by_type.items()
    }
    merged = list(resolved)
    for item in fallback:
        kind = str(item.get("type") or "attachment")
        if missing_by_type.get(kind, 0) > 0:
            merged.append(item)
            missing_by_type[kind] -= 1
    merged.extend(_fallback_planned_materials({}, media_refs))
    return merged
