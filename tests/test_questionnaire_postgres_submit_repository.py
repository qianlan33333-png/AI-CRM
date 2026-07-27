from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aicrm_next.extensions.forms.questionnaire import repo as questionnaire_repo
from aicrm_next.extensions.forms.questionnaire.repo import PostgresQuestionnaireReadRepository


class _Result:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row or {}

    def fetchone(self) -> dict[str, Any]:
        return self._row


class _Transaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def transaction(self) -> _Transaction:
        return _Transaction()

    def execute(self, sql: str, params: tuple[Any, ...]) -> _Result:
        self.calls.append((sql, params))
        if "FROM crm_user_identity identity" in sql:
            return _Result(
                {
                    "unionid": "union_questionnaire_submit_499",
                    "external_userid": "wm_submit_001",
                    "mobile": "13770938680",
                    "mobile_normalized": "13770938680",
                    "status": "active",
                    "matched_unionid": True,
                    "matched_external_userid": True,
                    "matched_openid": False,
                    "matched_mobile": True,
                }
            )
        if "INSERT INTO questionnaire_submissions" in sql:
            return _Result({"id": 901, "submitted_at": datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc)})
        return _Result()


def test_postgres_questionnaire_submit_writes_submission_and_answer_snapshots(monkeypatch) -> None:
    monkeypatch.setattr(questionnaire_repo, "_jsonb", lambda value: value)
    connection = _Connection()
    repository = PostgresQuestionnaireReadRepository(database_url="postgresql://example/aicrm")
    repository._connect = lambda: connection  # type: ignore[method-assign]
    repository.list_questions = lambda questionnaire_id: [  # type: ignore[method-assign]
        {
            "id": 11,
            "type": "mobile",
            "title": "请填写你要激活的手机号",
            "options": [],
        },
        {
            "id": 12,
            "type": "single_choice",
            "title": "激活状态",
            "options": [{"id": 31, "label": "已激活", "value": "31", "score": 10, "tag_codes": ["activated"]}],
        },
    ]

    submission = repository.create_submission(
        {
            "questionnaire_id": 499,
            "slug": "hxc-499-member",
            "answers": {"11": "13770938680", "12": 31},
            "result_json": {"score": 10},
            "source_json": {"source_channel": "h5"},
            "respondent_identity": {"unionid": "union_questionnaire_submit_499"},
            "external_userid": "wm_submit_001",
            "follow_user_userid": "LinKaiYan",
            "matched_by": "unionid",
            "final_tags": ["activated"],
            "result_token": "result_grant_postgres_contract_001",
            "status": "submitted",
        }
    )

    assert submission["submission_id"] == "901"
    assert submission["result_token"] == "result_grant_postgres_contract_001"
    assert submission["external_userid"] == "wm_submit_001"
    assert submission["follow_user_userid"] == "LinKaiYan"
    assert submission["matched_by"] == "unionid"
    assert submission["mobile"] == "13770938680"
    assert submission["score"] == 10
    assert len(connection.calls) == 6
    assert "pg_advisory_xact_lock" in connection.calls[1][0]
    assert "FROM questionnaire_submissions" in connection.calls[2][0]

    submission_params = connection.calls[3][1]
    assert submission_params[0] == 499
    assert submission_params[1] == "union_questionnaire_submit_499"
    assert submission_params[2] == "LinKaiYan"
    assert submission_params[3] == "h5"
    assert submission_params[7] == ["activated"]
    assert submission_params[9] == "result_grant_postgres_contract_001"

    mobile_answer_params = connection.calls[4][1]
    assert mobile_answer_params[1] == 11
    assert mobile_answer_params[2] == "mobile"
    assert mobile_answer_params[8] == "13770938680"

    choice_answer_params = connection.calls[5][1]
    assert choice_answer_params[1] == 12
    assert choice_answer_params[4] == [31]
    assert choice_answer_params[5] == ["已激活"]
    assert choice_answer_params[9] == 10
