"""Questionnaire compatibility exports for the platform-owned public grant."""

from aicrm_next.admin_auth.public_result_grant import (
    DEFAULT_RESULT_GRANT_TTL_SECONDS,
    MAX_RESULT_GRANT_TTL_SECONDS,
    QuestionnaireResultGrant,
    RESULT_GRANT_COOKIE_NAME,
    RESULT_GRANT_PURPOSE,
    issue_questionnaire_result_grant,
    questionnaire_result_token_from_grant,
    result_grant_cookie_path,
)

__all__ = [
    "DEFAULT_RESULT_GRANT_TTL_SECONDS",
    "MAX_RESULT_GRANT_TTL_SECONDS",
    "QuestionnaireResultGrant",
    "RESULT_GRANT_COOKIE_NAME",
    "RESULT_GRANT_PURPOSE",
    "issue_questionnaire_result_grant",
    "questionnaire_result_token_from_grant",
    "result_grant_cookie_path",
]
