from __future__ import annotations

import json
from typing import Any

from aicrm_next.customer_read_model.admin_business_profile import get_customer_business_profile
from aicrm_next.customer_read_model.application import GetCustomerContextQuery
from aicrm_next.customer_read_model.dto import CustomerContextRequest


PLACEHOLDERS = {
    "问卷信息": "questionnaire",
    "最近20条聊天信息": "recent_messages",
    "用户标签": "tags",
    "激活信息": "activation",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def referenced_context_keys(*parts: str) -> set[str]:
    blob = "\n".join(_text(part) for part in parts)
    return {key for placeholder, key in PLACEHOLDERS.items() if f"{{{{{placeholder}}}}}" in blob}


def _format_json_block(value: Any) -> str:
    if value in (None, "", [], {}):
        return "暂无"
    return json.dumps(value, ensure_ascii=False, default=str, indent=2)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _audience_payload(bound_audience_context: dict[str, Any]) -> dict[str, Any]:
    event = dict(bound_audience_context.get("member_event") or {})
    current = dict(bound_audience_context.get("member_current") or {})
    payload = event.get("payload_json") if isinstance(event.get("payload_json"), dict) else {}
    if not payload:
        payload = current.get("payload_json") if isinstance(current.get("payload_json"), dict) else {}
    return dict(payload or {})


def _questionnaire_answer_text(row: dict[str, Any]) -> str:
    question_type = _text(row.get("question_type")).lower()
    question = _text(row.get("question"))
    text_value = _text(row.get("text_value"))
    option_values = [_text(item) for item in _json_list(row.get("selected_option_texts_snapshot")) if _text(item)]
    answer = text_value or "；".join(option_values)
    if not answer:
        return "未填写"
    if question_type in {"mobile", "phone", "tel"} or any(token in question for token in ("手机号", "手机", "电话")):
        return "已填写（已脱敏）"
    return answer


def _normalize_questionnaire_answer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "questionnaire_id": _text(row.get("questionnaire_id")),
        "questionnaire_title": _text(row.get("questionnaire_title")),
        "submission_id": _text(row.get("submission_id")),
        "submitted_at": _text(row.get("submitted_at")),
        "question_id": _text(row.get("question_id")),
        "question": _text(row.get("question")) or "未命名问题",
        "answer": _questionnaire_answer_text(row),
    }


def _bound_questionnaire_answers(
    bound_audience_context: dict[str, Any],
    *,
    external_userid: str,
    repository: Any | None,
) -> list[dict[str, Any]]:
    payload = _audience_payload(bound_audience_context)
    submission_id = _safe_int(payload.get("submission_id") or payload.get("questionnaire_submission_id"))
    questionnaire_id = _safe_int(payload.get("questionnaire_id"))
    answers = payload.get("questionnaire_answers") or payload.get("answers")
    if isinstance(answers, list) and answers:
        return [_normalize_questionnaire_answer(item) for item in answers if isinstance(item, dict)]
    if (submission_id <= 0 and questionnaire_id <= 0) or repository is None:
        return []
    rows = repository.list_questionnaire_submission_answers(
        submission_id=submission_id,
        questionnaire_id=questionnaire_id,
        external_userid=external_userid,
    )
    return [_normalize_questionnaire_answer(row) for row in rows]


def _questionnaire_block(profile: dict[str, Any], *, bound_answers: list[dict[str, Any]] | None = None, bound_payload: dict[str, Any] | None = None) -> str:
    if bound_answers:
        return _format_json_block(bound_answers)
    bound_payload = dict(bound_payload or {})
    answers = (
        bound_payload.get("questionnaire_answers")
        or bound_payload.get("answers")
        or dict(profile.get("business_profile") or {}).get("questionnaire_answers")
        or dict(profile.get("marketing_profile") or {}).get("matched_questions")
        or []
    )
    return _format_json_block(answers)


def _messages_block(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in messages[:20]:
        sender = _text(item.get("sender") or item.get("from"))
        content = _text(item.get("content") or item.get("summary"))
        send_time = _text(item.get("send_time") or item.get("event_time"))
        if content:
            lines.append(f"{send_time} {sender}: {content}".strip())
    return "\n".join(lines) if lines else "暂无"


def _activation_block(customer: dict[str, Any]) -> str:
    return _format_json_block(
        {
            "class_user_status": customer.get("class_user_status") or {},
            "marketing_summary": customer.get("marketing_summary") or {},
            "marketing_profile": customer.get("marketing_profile") or {},
        }
    )


def build_agent_context(
    external_userid: str,
    referenced_keys: set[str],
    *,
    agent_code: str = "",
    batch_id: str = "",
    external_event_id: str = "",
    repository: Any | None = None,
) -> dict[str, Any]:
    context = GetCustomerContextQuery()(
        CustomerContextRequest(external_userid=external_userid, recent_message_limit=20, timeline_limit=20)
    )
    customer = dict(context.get("customer") or context.get("profile") or {})
    business_profile = {}
    if "questionnaire" in referenced_keys or "tags" in referenced_keys:
        profile_result = get_customer_business_profile(external_userid, limit=20)
        business_profile = dict(profile_result.get("business_profile") or {})
        customer = {**customer, "business_profile": business_profile}
    tags = list(business_profile.get("tags") or customer.get("tags") or [])
    recent_messages = list(context.get("recent_messages") or [])
    bound_audience_context: dict[str, Any] = {}
    if referenced_keys & {"questionnaire", "activation"}:
        if repository is None:
            from .repository import build_automation_agent_repository

            repository = build_automation_agent_repository()
        bound_audience_context = repository.get_bound_audience_context_for_item(
            batch_id=batch_id,
            agent_code=agent_code,
            external_userid=external_userid,
        )
    bound_payload = _audience_payload(bound_audience_context)
    bound_questionnaire_answers = (
        _bound_questionnaire_answers(bound_audience_context, external_userid=external_userid, repository=repository)
        if "questionnaire" in referenced_keys
        else []
    )
    owner_userid = _text(
        customer.get("owner_userid")
        or dict(bound_audience_context.get("member_event") or {}).get("owner_userid")
        or dict(bound_audience_context.get("member_current") or {}).get("owner_userid")
        or bound_payload.get("owner_userid")
        or dict(context.get("identity_binding_summary") or {}).get("owner_userid")
        or dict(customer.get("binding") or {}).get("owner_userid")
    )
    blocks: dict[str, str] = {}
    if "questionnaire" in referenced_keys:
        blocks["问卷信息"] = _questionnaire_block(
            {**customer, "business_profile": business_profile},
            bound_answers=bound_questionnaire_answers,
            bound_payload=bound_payload,
        )
    if "recent_messages" in referenced_keys:
        blocks["最近20条聊天信息"] = _messages_block(recent_messages)
    if "tags" in referenced_keys:
        blocks["用户标签"] = _format_json_block(tags)
    if "activation" in referenced_keys:
        blocks["激活信息"] = _activation_block(customer)
    return {
        "owner_userid": owner_userid,
        "customer": customer,
        "recent_messages": recent_messages[:20],
        "tags": tags,
        "blocks": blocks,
        "referenced_context_keys": sorted(referenced_keys),
        "bound_audience_context": {
            "batch_id": _text(batch_id),
            "agent_code": _text(agent_code),
            "external_event_id": _text(external_event_id),
            "payload_json": bound_payload,
            "questionnaire_answer_count": len(bound_questionnaire_answers),
            "member_event_id": dict(bound_audience_context.get("member_event") or {}).get("id"),
            "member_current_id": dict(bound_audience_context.get("member_current") or {}).get("id"),
        },
        "raw_context": context,
    }


def render_chinese_placeholders(text: str, blocks: dict[str, str]) -> str:
    rendered = _text(text)
    for placeholder in PLACEHOLDERS:
        rendered = rendered.replace(f"{{{{{placeholder}}}}}", _text(blocks.get(placeholder)))
    return rendered
