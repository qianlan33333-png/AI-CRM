from __future__ import annotations

from time import time

from aicrm_next.extensions.forms.questionnaire.result_access import (
    RESULT_GRANT_COOKIE_NAME,
    issue_questionnaire_result_grant,
    questionnaire_result_token_from_grant,
    result_grant_cookie_path,
)


def test_result_grant_is_bound_to_slug_and_keeps_token_inside_http_only_cookie(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "questionnaire-result-grant")
    now = int(time())
    grant = issue_questionnaire_result_grant(
        slug="hxc-activation-v1",
        result_access_token="result-token-a",
        now=now,
    )

    assert grant.cookie_name == RESULT_GRANT_COOKIE_NAME
    assert grant.cookie_path == result_grant_cookie_path("hxc-activation-v1")
    assert questionnaire_result_token_from_grant(
        grant.cookie_value,
        slug="hxc-activation-v1",
        now=now,
    ) == "result-token-a"
    assert questionnaire_result_token_from_grant(
        grant.cookie_value,
        slug="other-questionnaire",
        now=now,
    ) is None


def test_result_grant_rejects_tampering_and_expiry(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "questionnaire-result-grant-expiry")
    now = int(time())
    grant = issue_questionnaire_result_grant(
        slug="hxc-activation-v1",
        result_access_token="result-token-expiring",
        now=now,
        ttl_seconds=60,
    )

    assert questionnaire_result_token_from_grant(
        f"{grant.cookie_value}tampered",
        slug="hxc-activation-v1",
        now=now,
        ttl_seconds=60,
    ) is None
    assert questionnaire_result_token_from_grant(
        grant.cookie_value,
        slug="hxc-activation-v1",
        now=now + 61,
        ttl_seconds=60,
    ) is None
