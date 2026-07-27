from __future__ import annotations

from datetime import datetime, timezone

from aicrm_next.identity_contact.application import ResolvePersonIdentityQuery
from aicrm_next.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.identity_contact.resolver import IdentityConflictError
from aicrm_next.shared.repository_provider import RepositoryProviderError
from aicrm_next.shared.runtime import production_data_ready
from aicrm_next.shared.typing import JsonDict

from .repo import MessageArchiveRepository, build_message_archive_repository


def _normalize_limit(value: int | None, *, default: int = 20) -> int:
    if value is None:
        return default
    return max(1, min(int(value), 200))


def _normalize_offset(value: int | None) -> int:
    return max(0, int(value or 0))


def _normalize_chat_type(value: str | None) -> str:
    chat_type = str(value or "").strip().lower()
    if chat_type and chat_type not in {"private", "group"}:
        raise ValueError("chat_type must be private or group")
    return chat_type


def _normalize_chat_scene(value: str | None) -> str:
    scene = str(value or "").strip().lower()
    aliases = {"私信": "private", "single": "private", "private": "private", "群聊": "group", "group": "group"}
    normalized = aliases.get(scene, scene)
    if normalized not in {"private", "group"}:
        raise ValueError("chat_scene must be private or group")
    return normalized


def _timestamp_filter(value: int | None, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required")
    if value < 0:
        raise ValueError(f"{name} must be a Unix timestamp in seconds")
    if value > 9_999_999_999:
        raise ValueError(f"{name} must be a Unix timestamp in seconds, not milliseconds")
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _source_status() -> tuple[str, str]:
    if production_data_ready():
        return "message_archive_read_model", "primary"
    return "message_archive_fixture", "fixture"


def _production_unavailable(exc: Exception) -> JsonDict:
    return {
        "ok": False,
        "degraded": True,
        "messages": [],
        "items": [],
        "count": 0,
        "source_status": "production_unavailable",
        "read_model_status": "unavailable",
        "fallback_used": False,
        "route_owner": "ai_crm_next",
        "error_code": "message_archive_read_unavailable",
        "page_error": str(exc).replace("production/postgres/legacy facade data", "production/postgres message archive data"),
        "status_code": 503,
    }


class ListArchivedMessagesQuery:
    def __init__(self, repo: MessageArchiveRepository | None = None) -> None:
        self._repo = repo

    def execute(
        self,
        *,
        external_userid: str,
        chat_type: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> JsonDict:
        try:
            normalized_chat_type = _normalize_chat_type(chat_type)
            safe_limit = _normalize_limit(limit)
            safe_offset = _normalize_offset(offset)
            repo = self._repo or build_message_archive_repository()
            rows = repo.list_messages(
                str(external_userid or "").strip(),
                chat_type=normalized_chat_type,
                limit=safe_limit,
                offset=safe_offset,
            )
        except RepositoryProviderError as exc:
            return _production_unavailable(exc)
        except ValueError as exc:
            return _input_error(str(exc))
        except Exception as exc:
            if production_data_ready():
                return _production_unavailable(exc)
            raise
        source_status, read_model_status = _source_status()
        return {
            "ok": True,
            "messages": rows,
            "items": rows,
            "count": len(rows),
            "external_userid": str(external_userid or "").strip(),
            "limit": safe_limit,
            "offset": safe_offset,
            "filters": {"chat_type": normalized_chat_type},
            "source_status": source_status,
            "read_model_status": read_model_status,
            "fallback_used": False,
            "route_owner": "ai_crm_next",
            "status_code": 200,
        }

    __call__ = execute


class SearchArchivedMessagesQuery:
    def __init__(self, repo: MessageArchiveRepository | None = None) -> None:
        self._repo = repo

    def execute(
        self,
        *,
        external_userid: str,
        keyword: str,
        chat_type: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> JsonDict:
        if not str(external_userid or "").strip() or not str(keyword or "").strip():
            return _input_error("external_userid and keyword are required")
        try:
            normalized_chat_type = _normalize_chat_type(chat_type)
            safe_limit = _normalize_limit(limit)
            safe_offset = _normalize_offset(offset)
            repo = self._repo or build_message_archive_repository()
            rows = repo.search_messages(
                external_userid=str(external_userid or "").strip(),
                keyword=str(keyword or "").strip(),
                chat_type=normalized_chat_type,
                limit=safe_limit,
                offset=safe_offset,
            )
        except RepositoryProviderError as exc:
            return _production_unavailable(exc)
        except ValueError as exc:
            return _input_error(str(exc))
        except Exception as exc:
            if production_data_ready():
                return _production_unavailable(exc)
            raise
        source_status, read_model_status = _source_status()
        return {
            "ok": True,
            "messages": rows,
            "items": rows,
            "count": len(rows),
            "external_userid": str(external_userid or "").strip(),
            "keyword": str(keyword or "").strip(),
            "limit": safe_limit,
            "offset": safe_offset,
            "filters": {"chat_type": normalized_chat_type},
            "source_status": source_status,
            "read_model_status": read_model_status,
            "fallback_used": False,
            "route_owner": "ai_crm_next",
            "status_code": 200,
        }

    __call__ = execute


class ListExternalChatRecordsQuery:
    def __init__(
        self,
        repo: MessageArchiveRepository | None = None,
        identity_query: ResolvePersonIdentityQuery | None = None,
    ) -> None:
        self._repo = repo
        self._identity_query = identity_query or ResolvePersonIdentityQuery()

    def execute(
        self,
        *,
        external_userid: str = "",
        unionid: str = "",
        mobile: str = "",
        start_time: int | None = None,
        chat_scene: str = "",
        with_userid: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> JsonDict:
        try:
            identity = self._resolve_identity(external_userid=external_userid, unionid=unionid, mobile=mobile)
            if not identity.get("external_userid"):
                return _not_found("user not found")
            normalized_scene = _normalize_chat_scene(chat_scene)
            normalized_start_time = _timestamp_filter(start_time, "start_time")
            safe_limit = max(1, min(int(limit or 20), 20))
            safe_offset = _normalize_offset(offset)
            normalized_with_userid = str(with_userid or "HuangYouCan").strip() or "HuangYouCan"
            repo = self._repo or build_message_archive_repository()
            rows, total = repo.list_external_chat_records(
                external_userid=str(identity["external_userid"]),
                chat_scene=normalized_scene,
                start_time=normalized_start_time,
                with_userid=normalized_with_userid if normalized_scene == "private" else "",
                limit=safe_limit,
                offset=safe_offset,
            )
        except RepositoryProviderError as exc:
            return _production_unavailable(exc)
        except IdentityConflictError as exc:
            return _identity_conflict(str(exc))
        except ValueError as exc:
            return _input_error(str(exc))
        except Exception as exc:
            if production_data_ready():
                return _production_unavailable(exc)
            raise
        source_status, read_model_status = _source_status()
        return {
            "ok": True,
            "items": rows,
            "messages": rows,
            "total": total,
            "count": len(rows),
            "external_userid": str(identity["external_userid"]),
            "matched_by": identity.get("matched_by") or "",
            "limit": safe_limit,
            "offset": safe_offset,
            "filters": {
                "chat_scene": normalized_scene,
                "start_time": normalized_start_time,
                "with_userid": normalized_with_userid if normalized_scene == "private" else "",
            },
            "source_status": source_status,
            "read_model_status": read_model_status,
            "fallback_used": False,
            "route_owner": "ai_crm_next",
            "status_code": 200,
        }

    def _resolve_identity(self, *, external_userid: str = "", unionid: str = "", mobile: str = "") -> JsonDict:
        external = str(external_userid or "").strip()
        normalized_unionid = str(unionid or "").strip()
        normalized_mobile = str(mobile or "").strip()
        if not external and not normalized_unionid and not normalized_mobile:
            raise ValueError("one of mobile, unionid, or external_userid is required")
        resolved = self._identity_query(
            ResolvePersonIdentityRequest(
                external_userid=external or None,
                unionid=normalized_unionid or None,
                mobile=normalized_mobile or None,
            )
        )
        if not resolved:
            return {}
        return {
            "external_userid": resolved.external_userid or "",
            "matched_by": resolved.matched_by or ("external_userid" if external else "unionid" if normalized_unionid else "mobile"),
        }

    __call__ = execute


def deprecated_messages_route(*, replacement_route: str = "") -> JsonDict:
    return {
        "ok": False,
        "error_code": "messages_route_deprecated",
        "message": "This legacy messages route has been replaced by exact Next routes.",
        "replacement_route": replacement_route,
        "route_owner": "ai_crm_next",
        "source_status": "deprecated",
        "read_model_status": "not_applicable",
        "fallback_used": False,
        "status_code": 410,
    }


def blocked_messages_side_effect(*, action: str) -> JsonDict:
    return {
        "ok": False,
        "error_code": "external_call_blocked",
        "message": "Message write/send routes are blocked in this Legacy Exit replacement phase.",
        "action": action,
        "route_owner": "ai_crm_next",
        "source_status": "external_call_blocked",
        "side_effect_plan": {
            "would_call": "wecom_message_send",
            "real_external_call_executed": False,
            "next_step": "requires_platform_jobs",
        },
        "status_code": 503,
    }


def _input_error(message: str) -> JsonDict:
    return {
        "ok": False,
        "error": message,
        "error_code": "invalid_request",
        "source_status": "input_error",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "status_code": 400,
    }


def _not_found(message: str) -> JsonDict:
    return {
        "ok": False,
        "error": message,
        "error_code": "not_found",
        "source_status": "not_found",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "status_code": 404,
    }


def _identity_conflict(message: str) -> JsonDict:
    return {
        "ok": False,
        "error": message,
        "error_code": "identity_conflict",
        "source_status": "identity_conflict",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "status_code": 409,
    }
