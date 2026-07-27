from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from aicrm_next.platform_foundation.external_effects.models import public_datetime, utcnow

QUESTIONNAIRE_EXTERNAL_PUSH_MODE = "queue"


def build_questionnaire_external_effect_payload(
    *,
    questionnaire: dict[str, Any],
    submission: dict[str, Any],
    computed_result: dict[str, Any],
    target_url: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = dict(questionnaire.get("external_push_config") or {})
    request_body = body or build_questionnaire_external_push_payload(
        questionnaire=questionnaire,
        submission=submission,
        computed_result=computed_result,
    )
    payload: dict[str, Any] = {
        "webhook_url": target_url,
        "body": request_body,
        "signature": {
            "mode": "aicrm_hmac_sha256",
            "credential_source": "registered_webhook_client",
        },
    }
    _copy_test_loopback_config(payload, config, request_body)
    return payload


def build_questionnaire_external_push_payload(
    *,
    questionnaire: dict[str, Any],
    submission: dict[str, Any],
    computed_result: dict[str, Any],
) -> dict[str, Any]:
    config = dict(questionnaire.get("external_push_config") or {})
    answer_snapshots = list(submission.get("answer_snapshots") or computed_result.get("answer_snapshots") or [])
    payload: dict[str, Any] = {
        "user_id": _external_push_user_id(submission),
        "questionnaire_title": _text(questionnaire.get("title") or questionnaire.get("name")),
        "submitted_at": _iso_datetime(submission.get("submitted_at") or submission.get("created_at")),
        "phone_number": _phone_number(answer_snapshots),
        "answers": _serialized_answers(answer_snapshots),
    }
    _copy_int(payload, "day", config.get("day") if "day" in config else questionnaire.get("external_push_day"))
    _copy_int(payload, "frequency", config.get("frequency") if "frequency" in config else questionnaire.get("external_push_frequency"))
    _copy_int(
        payload,
        "expires_at_ts",
        config.get("expires_at_ts") if "expires_at_ts" in config else questionnaire.get("external_push_expires_at_ts"),
    )
    push_type = _text(config.get("type") or questionnaire.get("external_push_type"))
    if push_type:
        payload["type"] = push_type
    remark = _text(config.get("remark") or questionnaire.get("external_push_remark"))
    if remark:
        payload["remark"] = remark
    assessment_result = computed_result.get("assessment_result")
    if isinstance(assessment_result, dict) and assessment_result:
        payload["assessment_result_snapshot"] = assessment_result
    for item in _custom_params(config.get("custom_params") or questionnaire.get("external_push_custom_params")):
        payload[item["name"]] = item["value"]
    return payload


def _copy_test_loopback_config(payload: dict[str, Any], config: dict[str, Any], body: dict[str, Any]) -> None:
    execution_scope = _text(config.get("execution_scope"))
    loopback_enabled = execution_scope == "test_loopback" or bool(config.get("test_loopback_enabled"))
    if not loopback_enabled:
        return
    payload["receiver_response_status"] = int(config.get("receiver_response_status") or config.get("test_receiver_response_status") or 200)
    payload["execution_scope"] = "test_loopback"
    payload["is_test"] = True
    payload["expected_payload_hash"] = _canonical_payload_hash(body)
    expires_at = _text(config.get("test_receiver_expires_at"))
    payload["test_receiver_expires_at"] = expires_at or public_datetime(utcnow() + timedelta(hours=12))


def _canonical_payload_hash(body: dict[str, Any]) -> str:
    canonical = json.dumps(body or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialized_answers(answer_snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in answer_snapshots:
        question_type = _text(item.get("question_type"))
        title = _text(item.get("question_title_snapshot"))
        if question_type == "multi_choice":
            answer: str | list[str] = _dedupe([_text(value) for value in item.get("selected_option_texts_snapshot") or []])
        elif question_type == "single_choice":
            answer = (_dedupe([_text(value) for value in item.get("selected_option_texts_snapshot") or []]) or [""])[0]
        elif question_type in {"textarea", "mobile"}:
            answer = _text(item.get("text_value"))
        else:
            continue
        serialized.append({"title": title, "answer": answer})
    return serialized


def _phone_number(answer_snapshots: list[dict[str, Any]]) -> str:
    for item in answer_snapshots:
        if _text(item.get("question_type")) != "mobile":
            continue
        return _text(item.get("text_value")) or "NULL"
    return "NULL"


def _external_push_user_id(submission: dict[str, Any]) -> str:
    for field in ["respondent_key", "external_userid", "unionid", "openid"]:
        value = _text(submission.get(field))
        if value:
            return value
    return ""


def _copy_int(payload: dict[str, Any], key: str, value: Any) -> None:
    if value in (None, ""):
        return
    payload[key] = int(value)


def _custom_params(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    reserved = {"user_id", "questionnaire_title", "submitted_at", "answers", "phone_number", "type", "expires_at_ts", "day", "frequency", "remark"}
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name or name in reserved:
            continue
        result.append({"name": name, "value": _text(item.get("value"))})
    return result


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _iso_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
