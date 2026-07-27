from __future__ import annotations

from aicrm_next.shared.typing import JsonDict


def _customer_360_identity(context: JsonDict, profile: JsonDict, unionid: str) -> JsonDict:
    identity = dict(profile.get("identity") or context.get("identity") or {})
    binding = dict(context.get("identity_binding_summary") or profile.get("binding") or {})
    return {
        "unionid": unionid or str(identity.get("unionid") or profile.get("unionid") or ""),
        "person_id": identity.get("person_id") or binding.get("person_id") or profile.get("person_id"),
        "external_userid": identity.get("external_userid") or binding.get("external_userid") or profile.get("external_userid") or "",
        "openid": identity.get("openid") or "",
        "mobile": identity.get("mobile") or binding.get("mobile") or profile.get("mobile") or "",
        "binding_status": binding.get("binding_status") or dict(profile.get("binding") or {}).get("binding_status") or "",
        "owner_userid": profile.get("owner_userid") or binding.get("owner_userid") or "",
    }


def _customer_360_orders_summary(profile: JsonDict) -> JsonDict:
    summary = dict(profile.get("orders_summary") or profile.get("commerce_summary") or {})
    return {
        "source_status": summary.get("source_status") or "not_connected",
        "paid_order_count": int(summary.get("paid_order_count") or summary.get("paid_count") or 0),
        "total_paid_amount": summary.get("total_paid_amount") or summary.get("paid_amount") or 0,
        "latest_order_at": summary.get("latest_order_at") or summary.get("last_paid_at") or "",
    }


def _customer_360_questionnaire_summary(profile: JsonDict) -> JsonDict:
    answers = _customer_360_questionnaire_answers(profile)
    latest = answers[0] if answers else {}
    return {
        "answer_count": len(answers),
        "latest_submission_id": str(latest.get("submission_id") or ""),
        "latest_submitted_at": str(latest.get("submitted_at") or ""),
        "answers": answers[:5],
    }


def _customer_360_questionnaire_answers(profile: JsonDict) -> list[JsonDict]:
    candidates = [
        dict(profile.get("marketing_profile") or {}).get("matched_questions"),
        dict(profile.get("sidebar_context") or {}).get("matched_questions"),
        dict(profile.get("marketing_summary") or {}).get("matched_questions"),
        profile.get("matched_questions"),
    ]
    answers: list[JsonDict] = []
    seen: set[tuple[str, str, str]] = set()
    for group in candidates:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            answer = {
                "questionnaire_id": str(item.get("questionnaire_id") or item.get("form_id") or ""),
                "questionnaire_title": str(item.get("questionnaire_title") or item.get("form_title") or item.get("title") or ""),
                "submission_id": str(item.get("submission_id") or ""),
                "submitted_at": str(item.get("submitted_at") or ""),
                "question": str(item.get("question") or item.get("title") or item.get("question_text") or ""),
                "answer": str(item.get("answer") or item.get("answer_text") or item.get("value") or ""),
            }
            if not answer["question"] and not answer["answer"]:
                continue
            key = (answer["submission_id"], answer["question"], answer["answer"])
            if key in seen:
                continue
            seen.add(key)
            answers.append(answer)
    return answers


def _customer_360_message_summary(profile: JsonDict, messages: list[JsonDict]) -> JsonDict:
    latest = messages[0] if messages else {}
    return {
        "recent_message_count": len(messages),
        "latest_message_at": profile.get("last_message_at") or latest.get("send_time") or latest.get("created_at") or "",
        "last_touch_at": profile.get("last_touch_at") or "",
    }


def _customer_360_user_ops_status(profile: JsonDict) -> JsonDict:
    status = dict(profile.get("class_user_status") or {})
    marketing = dict(profile.get("marketing_summary") or {})
    return {
        "current_status": status.get("current_status") or marketing.get("main_stage") or "",
        "signup_status": status.get("signup_status") or "",
        "activation_bucket": status.get("activation_bucket") or marketing.get("sub_stage") or "",
        "owner_userid": profile.get("owner_userid") or "",
        "updated_at": status.get("updated_at") or profile.get("updated_at") or "",
    }


def _customer_360_automation_status(profile: JsonDict) -> JsonDict:
    marketing_profile = dict(profile.get("marketing_profile") or {})
    marketing_summary = dict(profile.get("marketing_summary") or {})
    return {
        "stage_key": marketing_profile.get("stage_key") or "",
        "recommended_action": marketing_profile.get("recommended_action") or "",
        "signals": list(marketing_profile.get("signals") or []),
        "value_segment": marketing_summary.get("value_segment") or "",
        "source_status": "customer_read_model_projection",
    }


def _customer_360_touchpoints(items: list[JsonDict]) -> list[JsonDict]:
    touchpoints: list[JsonDict] = []
    for item in items[:10]:
        row = dict(item)
        touchpoints.append(
            {
                "touchpoint_key": str(row.get("event_id") or row.get("source_id") or ""),
                "touchpoint_type": str(row.get("event_type") or ""),
                "summary": str(row.get("summary") or row.get("title") or ""),
                "occurred_at": row.get("event_time") or row.get("created_at") or "",
                "source_table": str(row.get("source_table") or ""),
                "source_id": str(row.get("source_id") or ""),
            }
        )
    return touchpoints


def _customer_360_risk_flags(profile: JsonDict, identity: JsonDict, messages: list[JsonDict]) -> list[JsonDict]:
    flags: list[JsonDict] = []
    if not str(identity.get("unionid") or "").strip():
        flags.append({"flag": "missing_unionid", "severity": "red", "summary": "identity projection missing unionid"})
    if not str(identity.get("owner_userid") or profile.get("owner_userid") or "").strip():
        flags.append({"flag": "missing_owner", "severity": "yellow", "summary": "customer has no owner_userid"})
    if not profile.get("updated_at"):
        flags.append({"flag": "missing_projection_refresh", "severity": "yellow", "summary": "customer projection has no updated_at"})
    if not messages and not profile.get("last_message_at"):
        flags.append({"flag": "no_recent_message", "severity": "info", "summary": "no recent message in read model window"})
    return flags
