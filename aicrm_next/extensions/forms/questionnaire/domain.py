from __future__ import annotations

from typing import Any

from aicrm_next.navigation_target import completion_target_projection
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.mobile import MOBILE_VALIDATION_MESSAGE, normalize_mainland_mobile

CHOICE_QUESTION_TYPES = {"single_choice", "multi_choice"}


def _item_has_key(item: dict[str, Any], key: str) -> bool:
    return key in item and item.get(key) is not None


def _external_push_bool(item: dict[str, Any], config: dict[str, Any], key: str, config_key: str) -> bool:
    if _item_has_key(item, key):
        return bool(item.get(key))
    return bool(config.get(config_key))


def _external_push_text(item: dict[str, Any], config: dict[str, Any], key: str, config_key: str) -> str:
    if _item_has_key(item, key):
        return str(item.get(key) or "").strip()
    return str(config.get(config_key) or "").strip()


def _external_push_value(item: dict[str, Any], config: dict[str, Any], key: str, config_key: str) -> Any:
    if _item_has_key(item, key):
        return item.get(key)
    return config.get(config_key)


def normalize_questionnaire(item: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(item.get("enabled", not bool(item.get("is_disabled", False))))
    external_push_config = dict(item.get("external_push_config") or {})
    completion_target = completion_target_projection(
        item.get("completion_target_json") if item.get("completion_target_json") is not None else item.get("completion_target"),
        legacy_h5_url=item.get("redirect_url"),
    )
    assessment_config = dict(item.get("assessment_config") or item.get("result_config") or {})
    normalized = {
        "id": item["id"],
        "slug": str(item.get("slug") or "").strip(),
        "title": str(item.get("title") or item.get("name") or "").strip(),
        "name": str(item.get("name") or item.get("title") or "").strip(),
        "description": str(item.get("description") or "").strip(),
        "enabled": enabled,
        "is_disabled": not enabled,
        "status": str(item.get("status") or ("disabled" if not enabled else "published")),
        "version": int(item.get("version") or 1),
        "redirect_url": str(item.get("redirect_url") or "").strip(),
        "lead_channel_id": int(item.get("lead_channel_id") or 0) or None,
        **completion_target,
        "answer_display_mode": str(item.get("answer_display_mode") or "all_in_one").strip() or "all_in_one",
        "submit_button_text": str(item.get("submit_button_text") or "提交").strip(),
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
        "questions": [normalize_question(question) for question in item.get("questions", [])],
        "rules": list(item.get("rules") or item.get("score_rules") or []),
        "score_rules": list(item.get("score_rules") or item.get("rules") or []),
        "assessment_config": assessment_config,
        "result_config": dict(item.get("result_config") or assessment_config),
        "submissions_summary": dict(item.get("submissions_summary") or {}),
        "last_submitted_at": item.get("last_submitted_at") or "",
        "external_push_config": external_push_config,
        "external_push_enabled": _external_push_bool(item, external_push_config, "external_push_enabled", "enabled"),
        "external_push_url": _external_push_text(item, external_push_config, "external_push_url", "webhook_url"),
        "external_push_type": _external_push_text(item, external_push_config, "external_push_type", "type"),
        "external_push_expires_at_ts": _external_push_value(
            item,
            external_push_config,
            "external_push_expires_at_ts",
            "expires_at_ts",
        ),
        "external_push_day": _external_push_value(item, external_push_config, "external_push_day", "day"),
        "external_push_frequency": _external_push_value(
            item,
            external_push_config,
            "external_push_frequency",
            "frequency",
        ),
        "external_push_remark": _external_push_text(item, external_push_config, "external_push_remark", "remark"),
        "external_push_custom_params": list(
            _external_push_value(item, external_push_config, "external_push_custom_params", "custom_params") or []
        ),
        "submission_count": int(item.get("submission_count") or 0),
        "assessment_enabled": bool(item.get("assessment_enabled", False)),
        "is_assessment_template_asset": bool(item.get("assessment_enabled", False))
        and (
            str(assessment_config.get("asset_kind") or "").strip() == "assessment_template"
            or (
                not str(assessment_config.get("asset_kind") or "").strip()
                and str(assessment_config.get("template_id") or "").strip() == "siyuan_ip_business"
            )
        ),
    }
    normalized["question_count"] = len(normalized["questions"])
    normalized["public_path"] = f"/s/{normalized['slug']}"
    normalized["submitted_path"] = f"/s/{normalized['slug']}/submitted"
    return normalized


def normalize_question(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": question["id"],
        "type": str(question.get("type") or "single_choice"),
        "title": str(question.get("title") or "").strip(),
        "required": bool(question.get("required", False)),
        "sidebar_profile_field": str(question.get("sidebar_profile_field") or "").strip(),
        "options": [normalize_option(option) for option in question.get("options", [])],
        "placeholder_text": str(question.get("placeholder_text") or ""),
    }


def normalize_option(option: dict[str, Any]) -> dict[str, Any]:
    label = str(option.get("label") or option.get("option_text") or option.get("value") or "").strip()
    value = str(option.get("value") or option.get("id") or label).strip()
    return {
        "id": option.get("id") or value,
        "label": label,
        "option_text": label,
        "value": value,
        "tag_codes": list(option.get("tag_codes") or []),
        "score": int(option.get("score") or 0),
        "is_other": bool(option.get("is_other", False)),
        "other_placeholder": str(option.get("other_placeholder") or ""),
        "other_max_length": int(option.get("other_max_length") or 80),
    }


def summary_projection(item: dict[str, Any]) -> dict[str, Any]:
    questionnaire = normalize_questionnaire(item)
    keys = [
        "id",
        "slug",
        "title",
        "name",
        "description",
        "enabled",
        "is_disabled",
        "redirect_url",
        "lead_channel_id",
        "completion_target",
        "completion_target_enabled",
        "completion_target_type",
        "created_at",
        "updated_at",
        "status",
        "version",
        "question_count",
        "submission_count",
        "last_submitted_at",
        "assessment_enabled",
        "is_assessment_template_asset",
        "public_path",
        "submitted_path",
    ]
    return {key: questionnaire[key] for key in keys}


def admin_detail_projection(item: dict[str, Any]) -> dict[str, Any]:
    questionnaire = normalize_questionnaire(item)
    admin_questionnaire = {key: value for key, value in questionnaire.items() if key != "questions"}
    admin_questionnaire["questions"] = questionnaire["questions"]
    return {
        "questionnaire": admin_questionnaire,
        "questions": questionnaire["questions"],
        "external_push_config": questionnaire["external_push_config"],
    }


def public_projection(item: dict[str, Any]) -> dict[str, Any]:
    questionnaire = normalize_questionnaire(item)
    public_questionnaire = {
        key: questionnaire[key]
        for key in [
            "id",
            "slug",
            "title",
            "description",
            "enabled",
            "redirect_url",
            "completion_target",
            "completion_target_enabled",
            "completion_target_type",
            "answer_display_mode",
            "submit_button_text",
            "created_at",
            "updated_at",
        ]
    }
    public_questions = [
        {key: value for key, value in question.items() if key != "sidebar_profile_field"}
        for question in questionnaire["questions"]
    ]
    return {"questionnaire": public_questionnaire, "questions": public_questions}


def choice_answer_parts(raw_value: Any) -> tuple[list[str], str]:
    if raw_value is None or raw_value == "":
        return [], ""
    if isinstance(raw_value, dict):
        selected_value = None
        for key in ("selected_option_ids", "option_ids", "value"):
            if key in raw_value:
                selected_value = raw_value.get(key)
                break
        selected_ids = _choice_value_list(selected_value)
        other_text = ""
        for key in ("other_text", "text_value"):
            if key in raw_value:
                other_text = str(raw_value.get(key) or "").strip()
                break
        return selected_ids, other_text
    if isinstance(raw_value, list):
        return _choice_value_list(raw_value), ""
    return _choice_value_list(raw_value), ""


def _choice_value_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _question_label(question: dict[str, Any]) -> str:
    title = str(question.get("title") or "").strip()
    question_id = question.get("id")
    return f"{question_id} ({title})" if title else str(question_id)


def _option_lookup(question: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for option in question.get("options") or []:
        option_id = str(option.get("id") or "").strip()
        option_value = str(option.get("value") or option_id).strip()
        if option_id:
            lookup[option_id] = option
        if option_value:
            lookup[option_value] = option
    return lookup


def selected_choice_options(question: dict[str, Any], raw_value: Any) -> tuple[list[dict[str, Any]], str]:
    selected_ids, other_text = choice_answer_parts(raw_value)
    lookup = _option_lookup(question)
    selected_options: list[dict[str, Any]] = []
    seen_option_ids: set[str] = set()
    for selected_id in selected_ids:
        option = lookup.get(str(selected_id))
        if option is None:
            raise ContractError(f"question {_question_label(question)} selected option not found: {selected_id}")
        option_id = str(option.get("id") or selected_id)
        if option_id in seen_option_ids:
            continue
        seen_option_ids.add(option_id)
        selected_options.append(option)
    return selected_options, other_text


def validate_required_answers(questionnaire: dict[str, Any], answers: dict[str, Any]) -> None:
    for question in normalize_questionnaire(questionnaire)["questions"]:
        question_type = str(question.get("type") or "single_choice")
        value = answers.get(str(question["id"]))
        if question_type not in CHOICE_QUESTION_TYPES:
            if question["required"] and value in (None, "", []):
                raise ContractError(f"missing required answer: {question['id']}")
            if question_type == "mobile" and value not in (None, "", []) and not normalize_mainland_mobile(value):
                raise ContractError(f"question {_question_label(question)}: {MOBILE_VALIDATION_MESSAGE}")
            continue
        selected_ids, _other_text = choice_answer_parts(value)
        if question["required"] and not selected_ids:
            raise ContractError(f"missing required answer: {question['id']}")
        if not selected_ids:
            continue
        if question_type == "single_choice" and len(selected_ids) > 1:
            raise ContractError(f"question {_question_label(question)} single_choice only allows one selected option")
        selected_options, other_text = selected_choice_options(question, value)
        other_options = [option for option in selected_options if bool(option.get("is_other"))]
        if not other_options:
            continue
        if not other_text.strip():
            raise ContractError(f"question {_question_label(question)} other_text is required")
        other_max_length = int(other_options[0].get("other_max_length") or 80)
        if len(other_text.strip()) > other_max_length:
            raise ContractError(f"question {_question_label(question)} other_text length must be <= {other_max_length}")


def normalize_mobile_answer(value: Any) -> str:
    return normalize_mainland_mobile(value)


def extract_submission_mobile(questionnaire: dict[str, Any], answers: dict[str, Any], respondent_identity: dict[str, Any]) -> str:
    identity_mobile = normalize_mobile_answer((respondent_identity or {}).get("mobile"))
    if identity_mobile:
        return identity_mobile
    for question in normalize_questionnaire(questionnaire)["questions"]:
        field = str(question.get("sidebar_profile_field") or "").strip().lower()
        q_type = str(question.get("type") or "").strip().lower()
        title = str(question.get("title") or "").strip()
        if field not in {"mobile", "phone", "phone_number"} and q_type != "mobile" and "手机号" not in title:
            continue
        mobile = normalize_mobile_answer(answers.get(str(question["id"])))
        if mobile:
            return mobile
    for value in (answers or {}).values():
        mobile = normalize_mobile_answer(value)
        if mobile:
            return mobile
    return ""


def score_and_tags(questionnaire: dict[str, Any], answers: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    tags: list[str] = []
    for question in normalize_questionnaire(questionnaire)["questions"]:
        if str(question.get("type") or "") not in CHOICE_QUESTION_TYPES:
            continue
        raw_value = answers.get(str(question["id"]))
        selected_options, _other_text = selected_choice_options(question, raw_value)
        for option in selected_options:
            score += int(option.get("score") or 0)
            for tag_code in option.get("tag_codes") or []:
                if tag_code not in tags:
                    tags.append(str(tag_code))
    return score, tags
