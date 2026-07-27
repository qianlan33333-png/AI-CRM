from __future__ import annotations

import json

import pytest

from aicrm_next.crm.identity_contact import resolution_effects
from aicrm_next.crm.identity_contact.resolution_queue_port import (
    CompleteIdentityResolutionRequest,
    EnqueueIdentityResolutionRequest,
    build_identity_resolution_queue_port,
)


class _DbapiResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        if isinstance(self._row, list):
            return self._row[0] if self._row else None
        return self._row

    def fetchall(self):
        return self._row if isinstance(self._row, list) else [self._row]


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

    def fetchone(self):
        return self._row

    def scalar_one_or_none(self):
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


class _ScriptedDbapiConnection:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        compact_sql = " ".join(sql.split())
        self.calls.append((compact_sql, tuple(params)))
        response = self.responses.pop(0) if self.responses else None
        return _DbapiResult(response)


class _ScriptedSqlAlchemySession:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params):
        compact_sql = " ".join(str(statement).split())
        self.calls.append((compact_sql, dict(params)))
        response = self.responses.pop(0) if self.responses else None
        return _SqlAlchemyResult(response)


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


def test_claim_and_backfill_outcomes_use_owner_repository() -> None:
    connection = _ScriptedDbapiConnection(
        [
            [{"id": 73, "status": "pending", "attempts": 2}],
            None,
            None,
            None,
        ]
    )
    port = build_identity_resolution_queue_port()

    claimed = port.claim_due_dbapi(
        connection,
        limit=900,
        locked_by="worker-73",
        lease_seconds=5,
    )
    port.record_backfill_result_dbapi(
        connection,
        queue_id=73,
        outcome="resolved",
        result={"status": "success", "unionid": "union-73"},
    )
    port.record_backfill_result_dbapi(
        connection,
        queue_id=74,
        outcome="retryable",
        result={"status": "pending", "reason": "provider_busy"},
    )
    port.record_backfill_result_dbapi(
        connection,
        queue_id=75,
        outcome="failed",
        result={"status": "failed", "reason": "attempts_exhausted"},
    )

    assert claimed == [{"id": 73, "status": "pending", "attempts": 2}]
    claim_sql, claim_params = connection.calls[0]
    assert claim_sql.startswith("WITH due AS")
    assert claim_params[:2] == (500, 30)
    assert "SET status = 'resolved'" in connection.calls[1][0]
    assert connection.calls[1][1][0] == "union-73"
    assert "SET status = 'pending'" in connection.calls[2][0]
    assert connection.calls[2][1][0] == "provider_busy"
    assert "SET status = 'failed'" in connection.calls[3][0]
    assert connection.calls[3][1][0] == "attempts_exhausted"


def test_queue_queries_and_terminal_settlement_use_owner_repository() -> None:
    session = _ScriptedSqlAlchemySession(
        [
            {"id": 76, "status": "pending"},
            {"id": 176, "external_effect_job_id": 276, "result_status": "resolved"},
            76,
        ]
    )
    port = build_identity_resolution_queue_port()

    queue = port.get_queue_sqlalchemy(session, queue_id=76)
    receipt = port.get_completion_receipt_sqlalchemy(session, external_effect_job_id=276)
    settled = port.settle_terminal_sqlalchemy(
        session,
        queue_id=76,
        external_effect_job_id=276,
        status="held",
        error_code="provider_outcome_unknown",
    )

    assert queue == {"id": 76, "status": "pending"}
    assert receipt == {"id": 176, "external_effect_job_id": 276, "result_status": "resolved"}
    assert settled is True
    assert session.calls[0][0].startswith("SELECT * FROM crm_user_identity_resolution_queue")
    assert "FROM identity_resolution_completion_receipt" in session.calls[1][0]
    assert session.calls[2][1] == {
        "status": "held",
        "last_error": "provider_outcome_unknown",
        "queue_id": 76,
        "job_id": 276,
    }


def test_completion_receipt_and_queue_transition_share_callers_transaction() -> None:
    session = _ScriptedSqlAlchemySession([{"id": 177}, None])

    created = build_identity_resolution_queue_port().complete_sqlalchemy(
        session,
        CompleteIdentityResolutionRequest(
            job_id=277,
            attempt_id="attempt-277",
            queue_id=77,
            result_status="resolved",
            result_summary_json='{"unionid_present": true}',
            execution_id="execution-277",
            parent_execution_id="parent-277",
            resolved_unionid="union-77",
        ),
    )

    assert created is True
    assert len(session.calls) == 2
    assert session.calls[0][0].startswith("INSERT INTO identity_resolution_completion_receipt")
    assert session.calls[1][0].startswith("UPDATE crm_user_identity_resolution_queue")
    assert session.calls[1][1]["resolved_unionid"] == "union-77"


def test_duplicate_completion_receipt_does_not_repeat_queue_transition() -> None:
    session = _ScriptedSqlAlchemySession([None])

    created = build_identity_resolution_queue_port().complete_sqlalchemy(
        session,
        CompleteIdentityResolutionRequest(
            job_id=278,
            attempt_id="attempt-278",
            queue_id=78,
            result_status="conflict",
            result_summary_json="{}",
            execution_id="execution-278",
            conflict_reason="missing_unionid",
        ),
    )

    assert created is False
    assert len(session.calls) == 1


def test_pre_provider_reopen_preserves_exact_queue_job_pairs() -> None:
    connection = _ScriptedDbapiConnection([[{"id": 79}, {"id": 80}]])

    reopened = build_identity_resolution_queue_port().reopen_pre_provider_dbapi(
        connection,
        queue_ids=[79, 80],
        external_effect_job_ids=[279, 280],
    )

    assert reopened == [79, 80]
    sql, params = connection.calls[0]
    assert "FROM UNNEST" in sql
    assert "queue.external_effect_job_id = expected.job_id" in sql
    assert params == ([79, 80], [279, 280])
