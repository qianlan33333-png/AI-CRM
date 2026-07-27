# ruff: noqa: F401
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from typing import Any, Callable, Protocol
from uuid import uuid4

from aicrm_next.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.identity_contact.resolver import resolve_identity_with_dbapi, resolved_unionid
from aicrm_next.navigation_target.service import normalize_completion_target_for_storage
from aicrm_next.platform_foundation.internal_events.outbox import enqueue_transactional_internal_event_outbox
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.repository_provider import RepositoryProviderError, assert_repository_allowed
from aicrm_next.shared.runtime import production_data_ready, raw_database_url
from aicrm_next.shared.runtime_settings import runtime_setting
from .domain import CHOICE_QUESTION_TYPES, selected_choice_options
from .identity_resolution import enqueue_questionnaire_identity_resolution


class QuestionnaireRepository(Protocol):
    source_status: str
    read_model_status: str

    def list_questionnaires(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]: ...
    def get_questionnaire(self, questionnaire_id: int) -> dict[str, Any] | None: ...
    def get_questionnaire_by_slug(self, slug: str) -> dict[str, Any] | None: ...
    def list_questions(self, questionnaire_id: int) -> list[dict[str, Any]] | None: ...
    def get_results_summary(self, questionnaire_id: int) -> dict[str, Any] | None: ...
    def list_submissions(self, questionnaire_id: int, *, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int] | None: ...
    def list_external_submissions(
        self,
        *,
        filters: dict[str, Any],
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def save_questionnaire(self, payload: dict[str, Any], questionnaire_id: int | None = None) -> dict[str, Any]: ...
    def save_completion_operations(
        self,
        questionnaire_id: int,
        *,
        lead_channel_id: int | None,
        completion_target_json: dict[str, Any],
        redirect_url: str,
    ) -> dict[str, Any] | None: ...
    def save_external_push_operations(
        self,
        questionnaire_id: int,
        config: dict[str, Any],
    ) -> dict[str, Any] | None: ...
    def set_enabled(self, questionnaire_id: int, enabled: bool) -> dict[str, Any] | None: ...
    def delete_questionnaire(self, questionnaire_id: int) -> bool: ...
    def create_submission(
        self,
        payload: dict[str, Any],
        *,
        internal_event_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]: ...
    def get_submission(self, submission_id: str) -> dict[str, Any] | None: ...
    def get_submission_by_record_id(self, submission_id: str) -> dict[str, Any] | None: ...
    def find_submission_for_identity(self, questionnaire_id: int, identity: dict[str, Any]) -> dict[str, Any] | None: ...
    def latest_submission(self, questionnaire_id: int) -> dict[str, Any] | None: ...
    def export_submissions(self, questionnaire_id: int) -> dict[str, Any] | None: ...
    def get_app_setting(self, key: str) -> str | None: ...
    def list_external_push_log_threads(
        self,
        questionnaire_id: int | None = None,
        *,
        questionnaire_title: str = "",
        user_id: str = "",
        target_url: str = "",
        status: str = "",
        limit: int | None = 50,
    ) -> list[dict[str, Any]]: ...
    def count_external_push_logs(
        self,
        *,
        questionnaire_id: int | None = None,
        questionnaire_title: str = "",
        user_id: str = "",
        target_url: str = "",
        status: str = "",
        created_at_gte: str = "",
    ) -> int: ...
    def summarize_external_push_logs(self, questionnaire_id: int) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _identity_lookup_values(identity: dict[str, Any] | None) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for field in ("external_userid", "unionid", "openid", "respondent_key", "mobile"):
        value = _text((identity or {}).get(field)).strip()
        if not value:
            continue
        pair = (field, value)
        if pair in seen:
            continue
        seen.add(pair)
        values.append(pair)
    return values


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value)


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _json_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _jsonb(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(value, dumps=_json_dumps)


def _normalized_external_push_log(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["id"] = int(payload.get("id") or 0)
    payload["questionnaire_id"] = int(payload.get("questionnaire_id") or 0)
    payload["submission_record_id"] = int(payload.get("submission_record_id") or 0)
    payload["retry_from_log_id"] = int(payload.get("retry_from_log_id") or 0) or None
    payload["retry_attempt"] = int(payload.get("retry_attempt") or 0)
    payload["request_payload"] = _json_payload(payload.get("request_payload"))
    payload["response_status_code"] = payload.get("response_status_code")
    payload["questionnaire_title_snapshot"] = _text(payload.get("questionnaire_title_snapshot"))
    payload["user_id"] = _text(payload.get("user_id"))
    payload["target_url"] = _text(payload.get("target_url"))
    payload["response_body"] = _text(payload.get("response_body"))
    payload["status"] = _text(payload.get("status"))
    payload["failure_reason"] = _text(payload.get("failure_reason"))
    payload["created_at"] = _timestamp(payload.get("created_at"))
    payload["updated_at"] = _timestamp(payload.get("updated_at"))
    return payload


def _external_push_log_threads(
    rows: list[dict[str, Any]],
    *,
    status: str = "",
    limit: int | None = 50,
) -> list[dict[str, Any]]:
    normalized = [_normalized_external_push_log(row) for row in rows]
    roots = [row for row in normalized if not row.get("retry_from_log_id")]
    retries_by_root: dict[int, list[dict[str, Any]]] = {int(row["id"]): [] for row in roots}
    for row in normalized:
        root_id = int(row.get("retry_from_log_id") or 0)
        if root_id:
            retries_by_root.setdefault(root_id, []).append(row)
    threads: list[dict[str, Any]] = []
    normalized_status = _text(status).strip()
    for root in roots:
        retries = sorted(
            retries_by_root.get(int(root["id"]), []),
            key=lambda item: (_text(item.get("created_at")), int(item.get("id") or 0)),
            reverse=True,
        )
        latest = retries[0] if retries else root
        latest_status = _text(latest.get("status")).strip()
        if normalized_status and latest_status != normalized_status:
            continue
        threads.append(
            {
                **root,
                "is_retry": False,
                "retry_count": len(retries),
                "retries": retries,
                "latest_log": latest,
                "latest_status": latest_status,
                "latest_response_status_code": latest.get("response_status_code"),
                "latest_response_body": latest.get("response_body"),
                "latest_failure_reason": latest.get("failure_reason"),
                "latest_updated_at": latest.get("updated_at") or latest.get("created_at"),
                "has_retry": bool(retries),
                "can_retry": latest_status == "failed",
            }
        )
    threads.sort(
        key=lambda item: (_text(item.get("latest_updated_at")), int((item.get("latest_log") or {}).get("id") or 0)),
        reverse=True,
    )
    if limit is None:
        return threads
    return threads[: max(1, min(int(limit or 50), 200))]


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _slugify_questionnaire(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    if not base:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        base = f"q-{timestamp}-{uuid4().hex[:6]}"
    return base[:120]


def _external_push_payload(payload: dict[str, Any]) -> dict[str, Any]:
    config = dict(payload.get("external_push_config") or {})
    return {
        "enabled": _as_bool(payload.get("external_push_enabled", config.get("enabled"))),
        "url": _text(payload.get("external_push_url") or config.get("webhook_url")),
        "type": _text(payload.get("external_push_type") or config.get("type")),
        "expires_at_ts": _optional_int(payload.get("external_push_expires_at_ts", config.get("expires_at_ts"))),
        "day": _optional_int(payload.get("external_push_day", config.get("day"))),
        "frequency": _optional_int(payload.get("external_push_frequency", config.get("frequency"))),
        "remark": _text(payload.get("external_push_remark") or config.get("remark")),
        "custom_params": _json_list(payload.get("external_push_custom_params", config.get("custom_params"))),
    }


def _questionnaire_payload(payload: dict[str, Any], *, slug: str) -> dict[str, Any]:
    external_push = _external_push_payload(payload)
    assessment_config = _json_dict(payload.get("assessment_config") or payload.get("result_config"))
    title = _text(payload.get("title") or payload.get("name")).strip()
    completion_target = normalize_completion_target_for_storage(payload, legacy_url_key="redirect_url")
    return {
        "slug": slug,
        "name": _text(payload.get("name") or title).strip(),
        "title": title,
        "description": _text(payload.get("description")),
        "is_disabled": _as_bool(payload.get("is_disabled"), default=not _as_bool(payload.get("enabled"), default=True)),
        "redirect_url": _text(payload.get("redirect_url")),
        "completion_target_json": completion_target,
        "answer_display_mode": _text(payload.get("answer_display_mode") or "all_in_one") or "all_in_one",
        "assessment_enabled": _as_bool(payload.get("assessment_enabled")),
        "assessment_config": assessment_config,
        "external_push": external_push,
    }


def _answer_value_list(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value if item not in (None, "")]
    if raw_value == "":
        return []
    return [str(raw_value)]


def _text_answer(raw_value: Any) -> str:
    values = _answer_value_list(raw_value)
    return "、".join(values)


def _answer_snapshots(questions: list[dict[str, Any]], answers: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for question in questions:
        question_id = question.get("id")
        question_key = str(question_id)
        if question_key not in answers:
            continue
        question_type = _text(question.get("type") or "single_choice")
        raw_value = answers.get(question_key)
        selected_options: list[dict[str, Any]] = []
        other_text = ""
        if question_type in CHOICE_QUESTION_TYPES:
            selected_options, other_text = selected_choice_options(question, raw_value)
        selected_option_scores = [float(option.get("score") or 0) for option in selected_options]
        selected_option_tags: list[str] = []
        for option in selected_options:
            for tag_code in option.get("tag_codes") or []:
                tag_text = str(tag_code or "").strip()
                if tag_text and tag_text not in selected_option_tags:
                    selected_option_tags.append(tag_text)
        selected_other_text = other_text.strip() if any(bool(option.get("is_other")) for option in selected_options) else ""
        snapshots.append(
            {
                "question_id": question_id,
                "question_type": question_type,
                "question_title_snapshot": _text(question.get("title")),
                "selected_option_ids": [option.get("id") for option in selected_options],
                "selected_option_texts_snapshot": [_text(option.get("label") or option.get("option_text")) for option in selected_options],
                "selected_option_scores_snapshot": selected_option_scores,
                "selected_option_tags_snapshot": selected_option_tags,
                "text_value": _text_answer(raw_value) if question_type in {"textarea", "mobile"} else selected_other_text,
                "score_contribution": sum(selected_option_scores) if question_type not in {"textarea", "mobile"} else 0.0,
            }
        )
    return snapshots


def _answers_from_snapshots(answer_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for snapshot in answer_snapshots:
        key = str(snapshot.get("question_id"))
        question_type = _text(snapshot.get("question_type") or "single_choice")
        if question_type in {"textarea", "mobile"}:
            answers[key] = _text(snapshot.get("text_value"))
            continue
        selected = _json_list(snapshot.get("selected_option_ids"))
        other_text = _text(snapshot.get("text_value")).strip()
        if other_text:
            answers[key] = {"selected_option_ids": selected, "other_text": other_text}
        else:
            answers[key] = selected[0] if len(selected) == 1 else selected
    return answers


def _external_answer_projection(answer: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_title_snapshot": _text(answer.get("question_title_snapshot")),
        "selected_option_texts_snapshot": _json_list(answer.get("selected_option_texts_snapshot")),
        "text_value": _text(answer.get("text_value")),
        "score_contribution": float(answer.get("score_contribution") or 0),
    }


def _external_submission_projection(row: dict[str, Any], answers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "mobile": _text(row.get("mobile") or row.get("mobile_snapshot")),
        "unionid": _text(row.get("unionid")),
        "external_userid": _text(row.get("external_userid")),
        "submitted_at": _timestamp(row.get("submitted_at") or row.get("created_at")),
        "questionnaire_id": int(row.get("questionnaire_id") or 0),
        "questionnaire_title": _text(row.get("questionnaire_title") or row.get("title") or row.get("name")),
        "final_tags": _json_list(row.get("final_tags")),
        "assessment_result_snapshot": _json_dict(row.get("assessment_result_snapshot")),
        "answers": [_external_answer_projection(dict(answer)) for answer in answers or []],
    }


def _parse_comparable_timestamp(value: Any) -> datetime | None:
    text = _text(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        return datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=None)
    except ValueError:
        return None


def _matches_external_submission_filters(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    if _text(filters.get("mobile")) and _text(row.get("mobile") or row.get("mobile_snapshot")) != _text(filters.get("mobile")):
        return False
    if _text(filters.get("unionid")) and _text(row.get("unionid")) != _text(filters.get("unionid")):
        return False
    if _text(filters.get("external_userid")) and _text(row.get("external_userid")) != _text(filters.get("external_userid")):
        return False
    if filters.get("questionnaire_id") not in (None, "") and int(row.get("questionnaire_id") or 0) != int(filters.get("questionnaire_id") or 0):
        return False
    submitted_at = _parse_comparable_timestamp(row.get("submitted_at") or row.get("created_at"))
    submitted_from = _parse_comparable_timestamp(filters.get("submitted_from"))
    submitted_to = _parse_comparable_timestamp(filters.get("submitted_to"))
    if submitted_from and (not submitted_at or submitted_at < submitted_from):
        return False
    if submitted_to and (not submitted_at or submitted_at > submitted_to):
        return False
    return True


def _mobile_answer(questions: list[dict[str, Any]], answers: dict[str, Any]) -> str:
    for question in questions:
        if _text(question.get("type")) != "mobile":
            continue
        value = _text_answer(answers.get(str(question.get("id")))).strip()
        if value:
            return value
    return ""


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def _initial_questionnaires() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "slug": "hxc-activation-v1",
            "title": "黄小璨激活问卷",
            "name": "黄小璨激活问卷",
            "description": "用于收集用户激活状态和后续运营标签。",
            "enabled": True,
            "redirect_url": "/s/hxc-activation-v1/submitted",
            "submit_button_text": "提交问卷",
            "created_at": "2026-05-01T10:00:00Z",
            "updated_at": "2026-05-20T10:00:00Z",
            "submission_count": 1,
            "assessment_enabled": False,
            "external_push_config": {
                "enabled": False,
                "status": "stubbed",
                "note": "第一阶段不真实外发 webhook。",
            },
            "questions": [
                {
                    "id": "q_activation",
                    "type": "single_choice",
                    "title": "黄小璨是否已激活？",
                    "required": True,
                    "options": [
                        {
                            "id": "activated",
                            "label": "已激活",
                            "value": "activated",
                            "tag_codes": ["tag_hxc_activated"],
                            "score": 10,
                        },
                        {
                            "id": "not_activated",
                            "label": "未激活",
                            "value": "not_activated",
                            "tag_codes": ["tag_hxc_not_activated"],
                            "score": 0,
                        },
                    ],
                },
                {
                    "id": "q_interest",
                    "type": "multi_choice",
                    "title": "你关注哪些能力？",
                    "required": False,
                    "options": [
                        {"id": "private_domain", "label": "私域运营", "value": "private_domain", "tag_codes": ["tag_interest_private_domain"], "score": 3},
                        {"id": "ai_tools", "label": "AI 工具", "value": "ai_tools", "tag_codes": ["tag_interest_ai_tools"], "score": 3},
                    ],
                },
                {
                    "id": "q_note",
                    "type": "textarea",
                    "title": "还有什么想补充？",
                    "required": False,
                    "options": [],
                    "placeholder_text": "可填写你最想解决的问题",
                },
            ],
        },
        {
            "id": 2,
            "slug": "disabled-demo",
            "title": "停用问卷样例",
            "name": "停用问卷样例",
            "description": "用于验证 disabled questionnaire contract。",
            "enabled": False,
            "redirect_url": "",
            "submit_button_text": "提交",
            "created_at": "2026-05-02T10:00:00Z",
            "updated_at": "2026-05-20T10:00:00Z",
            "submission_count": 0,
            "assessment_enabled": False,
            "external_push_config": {"enabled": False, "status": "stubbed"},
            "questions": [
                {
                    "id": "q_disabled",
                    "type": "single_choice",
                    "title": "停用问卷问题",
                    "required": True,
                    "options": [{"id": "yes", "label": "是", "value": "yes", "tag_codes": [], "score": 0}],
                }
            ],
        },
    ]
