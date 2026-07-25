from __future__ import annotations

import json

import pytest

from aicrm_next.identity_contact import resolution_effects
from aicrm_next.identity_contact.resolution_queue_port import (
    EnqueueIdentityResolutionRequest,
    build_identity_resolution_queue_port,
)


class _DbapiResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _DbapiConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        compact_sql = " ".join(sql.split())
        self.calls.append((compact_sql, tuple(params)))
        return _DbapiResult(
            {
                "id": 71,
                "source_type": params[0],
                "source_key": params[1],
                "external_userid": params[3],
                "status": "pending",
            }
        )


class _SqlAlchemyResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def one(self):
        return self._row


class _SqlAlchemySession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params):
        compact_sql = " ".join(str(statement).split())
        self.calls.append((compact_sql, dict(params)))
        return _SqlAlchemyResult(
            {
                "id": 72,
                "source_type": params["source_type"],
                "source_key": params["source_key"],
                "external_userid": params["external_userid"],
                "status": "pending",
            }
        )


def test_dbapi_enqueue_uses_canonical_owner_sql_and_preserves_effect_lineage(monkeypatch) -> None:
    connection = _DbapiConnection()
    planned: list[dict] = []

    def plan_effect(conn, row, **kwargs):
        planned.append({"connection": conn, "row": row, **kwargs})
        return {"external_effect_job_id": 171, "execution_id": "exec-171"}

    monkeypatch.setattr(resolution_effects, "plan_identity_resolution_effect", plan_effect)
    request = EnqueueIdentityResolutionRequest(
        source_type="questionnaire_submission",
        source_key="respondent-71",
        reason="missing_unionid",
        source_route="questionnaire.identity_resolution.enqueue",
        external_userid="wm-71",
        openid="openid-71",
        mobile="13800138071",
        payload_json={"questionnaire_id": 71},
        parent_execution_id="parent-71",
    )

    result = build_identity_resolution_queue_port().enqueue_dbapi(connection, request)

    assert result == {"external_effect_job_id": 171, "execution_id": "exec-171"}
    sql, params = connection.calls[0]
    assert sql.startswith("INSERT INTO crm_user_identity_resolution_queue")
    assert params[:6] == (
        "questionnaire_submission",
        "respondent-71",
        "",
        "wm-71",
        "openid-71",
        "13800138071",
    )
    assert json.loads(params[6]) == {"questionnaire_id": 71}
    assert planned == [
        {
            "connection": connection,
            "row": {
                "id": 71,
                "source_type": "questionnaire_submission",
                "source_key": "respondent-71",
                "external_userid": "wm-71",
                "status": "pending",
            },
            "parent_execution_id": "parent-71",
            "source_route": "questionnaire.identity_resolution.enqueue",
        }
    ]


def test_sqlalchemy_enqueue_uses_same_owner_port(monkeypatch) -> None:
    session = _SqlAlchemySession()
    monkeypatch.setattr(
        resolution_effects,
        "plan_identity_resolution_effect",
        lambda _session, row, **_kwargs: {"queue_id": int(row["id"])},
    )
    request = EnqueueIdentityResolutionRequest(
        source_type="ai_audience_ops",
        source_key="audience-72",
        reason="identity_conflict",
        source_route="ai_audience_ops.identity_resolution.enqueue",
        mobile="13800138072",
        payload_json={"identity_type": "mobile"},
    )

    result = build_identity_resolution_queue_port().enqueue_sqlalchemy(session, request)

    assert result == {"queue_id": 72}
    sql, params = session.calls[0]
    assert sql.startswith("INSERT INTO crm_user_identity_resolution_queue")
    assert params["source_type"] == "ai_audience_ops"
    assert params["source_key"] == "audience-72"
    assert json.loads(params["payload_json"]) == {"identity_type": "mobile"}


def test_enqueue_rejects_non_idempotent_empty_source_key() -> None:
    with pytest.raises(ValueError, match="source_key is required"):
        build_identity_resolution_queue_port().enqueue_dbapi(
            _DbapiConnection(),
            EnqueueIdentityResolutionRequest(
                source_type="questionnaire_submission",
                source_key="",
                reason="missing_unionid",
                source_route="questionnaire.identity_resolution.enqueue",
            ),
        )
