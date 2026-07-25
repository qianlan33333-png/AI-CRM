from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any

from aicrm_next.shared.runtime_settings import managed_runtime_int
from uuid import uuid4

from aicrm_next.shared.signed_session import sign_session_payload, verify_session_payload


DEFAULT_RESULT_GRANT_TTL_SECONDS = 60 * 60
MAX_RESULT_GRANT_TTL_SECONDS = 24 * 60 * 60
RESULT_GRANT_COOKIE_NAME = "aicrm_questionnaire_result"
RESULT_GRANT_PURPOSE = "questionnaire_submission_result"


@dataclass(frozen=True)
class QuestionnaireResultGrant:
    cookie_name: str
    cookie_value: str
    max_age_seconds: int
    cookie_path: str


def issue_questionnaire_result_grant(
    *,
    slug: str,
    result_access_token: str,
    now: int | None = None,
    ttl_seconds: int | None = None,
) -> QuestionnaireResultGrant:
    normalized_slug = _text(slug)
    normalized_token = _text(result_access_token)
    if not normalized_slug or not normalized_token:
        raise ValueError("questionnaire result grant target is required")
    issued_at = int(time()) if now is None else int(now)
    ttl = _grant_ttl_seconds(ttl_seconds)
    payload = {
        "purpose": RESULT_GRANT_PURPOSE,
        "slug": normalized_slug,
        "result_access_token": normalized_token,
        "grant_id": uuid4().hex,
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    return QuestionnaireResultGrant(
        cookie_name=RESULT_GRANT_COOKIE_NAME,
        cookie_value=sign_session_payload(payload),
        max_age_seconds=ttl,
        cookie_path=result_grant_cookie_path(normalized_slug),
    )


def questionnaire_result_token_from_grant(
    cookie_value: str | None,
    *,
    slug: str,
    now: int | None = None,
    ttl_seconds: int | None = None,
) -> str | None:
    ttl = _grant_ttl_seconds(ttl_seconds)
    payload = verify_session_payload(cookie_value, max_age_seconds=ttl)
    if not payload:
        return None
    current_time = int(time()) if now is None else int(now)
    valid = bool(
        payload.get("purpose") == RESULT_GRANT_PURPOSE
        and _text(payload.get("slug")) == _text(slug)
        and _text(payload.get("result_access_token"))
        and _int(payload.get("iat")) > 0
        and _int(payload.get("iat")) <= current_time
        and _int(payload.get("exp")) >= current_time
        and _int(payload.get("exp")) - _int(payload.get("iat")) <= ttl
    )
    return _text(payload.get("result_access_token")) if valid else None


def result_grant_cookie_path(slug: str) -> str:
    return f"/api/h5/questionnaires/{_text(slug)}/result"


def _grant_ttl_seconds(override: int | None = None) -> int:
    raw_value: Any = override
    if raw_value is None:
        raw_value = managed_runtime_int(
            "AICRM_QUESTIONNAIRE_RESULT_GRANT_TTL_SECONDS",
            DEFAULT_RESULT_GRANT_TTL_SECONDS,
            minimum=60,
            maximum=MAX_RESULT_GRANT_TTL_SECONDS,
        )
    try:
        value = int(raw_value or DEFAULT_RESULT_GRANT_TTL_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_RESULT_GRANT_TTL_SECONDS
    return max(60, min(value, MAX_RESULT_GRANT_TTL_SECONDS))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
