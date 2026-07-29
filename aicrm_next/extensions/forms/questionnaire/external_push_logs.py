from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .repo import QuestionnaireRepository, build_questionnaire_repository

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_PLANNED = "planned"


class QuestionnaireExternalPushLogReadService:
    """Read-only view of retired synchronous questionnaire push history."""

    def __init__(self, repository: QuestionnaireRepository | None = None) -> None:
        self._repo = repository or build_questionnaire_repository()

    def questionnaire_logs(
        self,
        questionnaire_id: int,
        *,
        status: str = "",
        limit: int | str = 50,
    ) -> dict[str, Any] | None:
        questionnaire = self._repo.get_questionnaire(int(questionnaire_id))
        if not questionnaire:
            return None
        logs_path = f"/admin/questionnaires/{int(questionnaire_id)}/external-push-logs"
        normalized_status, effective_status = _normalize_status_filter(status)
        normalized_limit = _bounded_int(limit, default=50, minimum=1, maximum=200)
        rows = self._repo.list_external_push_log_threads(
            int(questionnaire_id),
            status=effective_status,
            limit=normalized_limit,
        )
        normalized_rows = [_normalize_thread(row) for row in rows]
        return {
            "is_global": False,
            "questionnaire": {**questionnaire, **_questionnaire_paths(questionnaire)},
            "paths": {"index": logs_path},
            "filters": {"status": normalized_status, "limit": normalized_limit},
            "status_options": _status_options(),
            "summary": self._repo.summarize_external_push_logs(int(questionnaire_id)),
            "logs": normalized_rows,
            "legacy_retry_retired": True,
            "source_status": getattr(self._repo, "source_status", "local_contract_probe"),
            "route_owner": "ai_crm_next",
            "fallback_used": False,
        }

    def global_logs(
        self,
        *,
        questionnaire_id: str = "",
        questionnaire_title: str = "",
        status: str = "",
        user_id: str = "",
        target_url: str = "",
        limit: int | str = 50,
    ) -> dict[str, Any]:
        normalized_questionnaire_id = _bounded_int(
            questionnaire_id,
            default=0,
            minimum=0,
            maximum=10**9,
        )
        questionnaire_filter = normalized_questionnaire_id or None
        normalized_status, effective_status = _normalize_status_filter(status)
        normalized_limit = _bounded_int(limit, default=50, minimum=1, maximum=200)
        rows = self._repo.list_external_push_log_threads(
            questionnaire_filter,
            questionnaire_title=_text(questionnaire_title),
            user_id=_text(user_id),
            target_url=_text(target_url),
            status=effective_status,
            limit=normalized_limit,
        )
        all_rows = self._repo.list_external_push_log_threads(None, limit=None)
        normalized_rows = [_normalize_thread(row) for row in rows]
        for row in normalized_rows:
            questionnaire_id_value = int(row.get("questionnaire_id") or 0)
            row["questionnaire_path"] = f"/admin/questionnaires/{questionnaire_id_value}"
            row["questionnaire_logs_path"] = f"/admin/questionnaires/{questionnaire_id_value}/external-push-logs"
        questionnaires, total_questionnaires = self._repo.list_questionnaires(limit=1000, offset=0)
        recent_since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        logs_path = "/admin/questionnaires/external-push-logs"
        return {
            "is_global": True,
            "questionnaire": None,
            "paths": {"index": logs_path},
            "filters": {
                "questionnaire_id": normalized_questionnaire_id,
                "questionnaire_title": _text(questionnaire_title),
                "status": normalized_status,
                "user_id": _text(user_id),
                "target_url": _text(target_url),
                "limit": normalized_limit,
            },
            "status_options": _status_options(),
            "summary": {
                "questionnaire_total_count": total_questionnaires,
                "enabled_questionnaire_count": sum(1 for item in questionnaires if _external_push_enabled(item)),
                "matched_questionnaire_count": len({int(row.get("questionnaire_id") or 0) for row in normalized_rows}),
                "total_log_count": self._repo.count_external_push_logs(),
                "current_failed_count": sum(1 for row in all_rows if row.get("can_retry")),
                "current_success_count": sum(1 for row in all_rows if _text(row.get("latest_status")) == STATUS_SUCCESS),
                "current_skipped_count": sum(1 for row in all_rows if _text(row.get("latest_status")) == STATUS_SKIPPED),
                "recent_success_count": self._repo.count_external_push_logs(
                    status=STATUS_SUCCESS,
                    created_at_gte=recent_since,
                ),
                "recent_failed_count": self._repo.count_external_push_logs(
                    status=STATUS_FAILED,
                    created_at_gte=recent_since,
                ),
                "global_switch_enabled": False,
                "global_switch_label": "旧入口已退休",
                "global_switch_tone": "muted",
            },
            "logs": normalized_rows,
            "legacy_retry_retired": True,
            "source_status": getattr(self._repo, "source_status", "local_contract_probe"),
            "route_owner": "ai_crm_next",
            "fallback_used": False,
        }


def _normalize_thread(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["first_status_label"] = _status_label(_text(payload.get("status")))
    payload["first_status_tone"] = _status_tone(_text(payload.get("status")))
    payload["effective_status_label"] = _status_label(_text(payload.get("latest_status")))
    payload["effective_status_tone"] = _status_tone(_text(payload.get("latest_status")))
    payload["latest_log"] = _normalize_log(dict(payload.get("latest_log") or {}))
    payload["latest_response_status_code"] = payload["latest_log"].get("response_status_code")
    payload["latest_response_body"] = payload["latest_log"].get("response_body")
    payload["latest_failure_reason"] = payload["latest_log"].get("failure_reason")
    payload["latest_updated_at"] = payload["latest_log"].get("updated_at") or payload["latest_log"].get("created_at")
    payload["retries"] = [_normalize_log(dict(item)) for item in payload.get("retries") or []]
    return payload


def _normalize_log(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["status_label"] = _status_label(_text(payload.get("status")))
    payload["status_tone"] = _status_tone(_text(payload.get("status")))
    return payload


def _status_options() -> list[dict[str, str]]:
    return [
        {"value": "", "label": "全部历史状态"},
        {"value": "failed_current", "label": "仅历史失败"},
        {"value": "success_current", "label": "仅历史成功"},
        {"value": "planned_current", "label": "仅历史补发计划"},
    ]


def _normalize_status_filter(value: Any) -> tuple[str, str]:
    normalized = _text(value)
    if normalized in {"failed", "failed_current"}:
        return "failed_current", STATUS_FAILED
    if normalized in {"success", "success_current"}:
        return "success_current", STATUS_SUCCESS
    if normalized in {"planned", "planned_current"}:
        return "planned_current", STATUS_PLANNED
    return "", ""


def _status_label(value: str) -> str:
    return {
        STATUS_SUCCESS: "成功",
        STATUS_FAILED: "失败",
        STATUS_SKIPPED: "已跳过",
        STATUS_PLANNED: "历史补发计划",
    }.get(_text(value), _text(value) or "未知")


def _status_tone(value: str) -> str:
    return {
        STATUS_SUCCESS: "ok",
        STATUS_FAILED: "danger",
        STATUS_SKIPPED: "warning",
        STATUS_PLANNED: "warning",
    }.get(_text(value), "muted")


def _questionnaire_paths(questionnaire: dict[str, Any]) -> dict[str, str]:
    questionnaire_id = int(questionnaire.get("id") or 0)
    slug = _text(questionnaire.get("slug"))
    return {
        "admin_path": f"/admin/questionnaires/{questionnaire_id}",
        "public_path": f"/s/{slug}" if slug else "",
        "external_push_logs_path": f"/admin/questionnaires/{questionnaire_id}/external-push-logs",
    }


def _external_push_enabled(questionnaire: dict[str, Any]) -> bool:
    config = questionnaire.get("external_push_config") if isinstance(questionnaire.get("external_push_config"), dict) else {}
    return _bool(questionnaire.get("external_push_enabled", config.get("enabled")))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(minimum, min(normalized, maximum))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


__all__ = ["QuestionnaireExternalPushLogReadService"]
