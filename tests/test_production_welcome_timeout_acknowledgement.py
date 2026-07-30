from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


class _ProductionWelcomeSession:
    def __init__(self) -> None:
        self.rollback_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def rollback(self) -> None:
        self.rollback_count += 1


def _acknowledge_with_candidates(monkeypatch, *, rows: list[dict]):
    from scripts.ops import acknowledge_production_welcome_timeout as welcome_timeout

    session = _ProductionWelcomeSession()
    monkeypatch.setattr(welcome_timeout, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        welcome_timeout,
        "_candidate_rows",
        lambda _session, _authorization: rows,
    )
    monkeypatch.setattr(
        welcome_timeout,
        "_load_authorization",
        lambda _path: {"error_code": "wecom_error_41050"},
    )
    monkeypatch.setenv("AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED", "1")
    result = welcome_timeout.acknowledge(
        manifest_path=Path("unused-by-test.json"),
        release_sha="a" * 40,
        authorization_base_sha=welcome_timeout.AUTHORIZATION_BASE_SHA,
        confirmation=welcome_timeout.EXPECTED_CONFIRMATION,
        actor="pytest",
        reason="production welcome history idempotency boundary test",
        apply=True,
    )
    return result, session


def test_absent_production_welcome_history_is_an_idempotent_noop(monkeypatch) -> None:
    result, session = _acknowledge_with_candidates(monkeypatch, rows=[])

    assert result == {
        "ok": True,
        "applied": False,
        "candidate_count": 0,
        "acknowledged_count": 0,
        "created_count": 0,
        "no_op_reason": "authorized_historical_terminal_absent",
        "replay_prohibited": True,
        "provider_success_claimed": False,
        "real_external_call_executed": False,
        "target_values_redacted": True,
    }
    assert session.rollback_count == 1


def test_production_welcome_history_rejects_multiple_candidates(monkeypatch) -> None:
    with pytest.raises(
        RuntimeError,
        match="expected at most one authorized production welcome terminal; found 2",
    ):
        _acknowledge_with_candidates(
            monkeypatch,
            rows=[SimpleNamespace(), SimpleNamespace()],
        )


@pytest.mark.postgres
def test_absent_production_welcome_history_is_a_postgres_noop(
    next_pg_schema,
    monkeypatch,
) -> None:
    from scripts.ops import acknowledge_production_welcome_timeout as welcome_timeout

    del next_pg_schema
    monkeypatch.setenv("AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED", "1")
    result = welcome_timeout.acknowledge(
        manifest_path=(
            Path(__file__).resolve().parents[1]
            / "docs"
            / "releases"
            / "production_welcome_timeout_acknowledgement.json"
        ),
        release_sha="a" * 40,
        authorization_base_sha=welcome_timeout.AUTHORIZATION_BASE_SHA,
        confirmation=welcome_timeout.EXPECTED_CONFIRMATION,
        actor="pytest",
        reason="postgres absent production welcome history no-op",
        apply=True,
    )

    assert result["applied"] is False
    assert result["candidate_count"] == 0
    assert result["acknowledged_count"] == 0
    assert result["created_count"] == 0
    assert result["no_op_reason"] == "authorized_historical_terminal_absent"
    assert result["real_external_call_executed"] is False


@pytest.mark.postgres
def test_exact_production_welcome_timeout_is_acknowledged_without_replay(
    next_pg_schema,
    monkeypatch,
) -> None:
    import psycopg

    from aicrm_next.insights.data_health import checks
    from scripts.ops.acknowledge_production_welcome_timeout import (
        AUTHORIZATION_BASE_SHA,
        EXPECTED_ATTEMPT_ID,
        EXPECTED_BUSINESS_ID,
        EXPECTED_CONFIRMATION,
        EXPECTED_EXECUTION_ID,
        EXPECTED_GRAPH_ID,
        EXPECTED_JOB_ID,
        acknowledge,
    )

    del next_pg_schema
    monkeypatch.setattr(checks, "EXTERNAL_EFFECT_TERMINAL_LOOKBACK_HOURS", 24 * 365 * 100)
    database_url = os.environ["DATABASE_URL"]
    target_id = f"redacted-production-welcome-target-{uuid4().hex}"

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO external_effect_job (
                id, effect_type, adapter_name, operation, target_type, target_id,
                business_type, business_id, source_module, source_route,
                idempotency_key, execution_mode, lane, status, attempt_count,
                max_attempts, last_error_code, last_error_message,
                side_effect_executed, provider_result_received,
                provider_call_started_at, reconciliation_required,
                worker_generation, policy_version, execution_id,
                created_at, updated_at, completed_at
            ) VALUES (
                %s, 'wecom.welcome_message.send', 'wecom_welcome_message', 'send',
                'external_user', %s,
                'channel_welcome_effect_graph', %s,
                'channel_entry.application', 'channel_entry.process_channel_entry',
                'production-welcome-timeout-job-2157', 'execute', 'wecom_interactive',
                'failed_terminal', 1, 5, 'wecom_error_41050',
                'invalid welcome code', TRUE, TRUE,
                TIMESTAMPTZ '2026-07-24T11:31:32.223640+08:00', FALSE, 1,
                'queue-v2-production-all-g1', %s,
                TIMESTAMPTZ '2026-07-24T11:30:38.262825+08:00',
                TIMESTAMPTZ '2026-07-24T11:31:32.739066+08:00',
                TIMESTAMPTZ '2026-07-24T11:31:32.739066+08:00'
            )
            """,
            (EXPECTED_JOB_ID, target_id, EXPECTED_BUSINESS_ID, EXPECTED_EXECUTION_ID),
        )
        connection.execute(
            """
            INSERT INTO external_effect_attempt (
                attempt_id, job_id, adapter_name, adapter_mode, operation,
                status, error_code, error_message, provider_call_started_at,
                worker_generation, started_at, completed_at
            ) VALUES (
                %s, %s, 'wecom_welcome_message', 'execute', 'send',
                'failed_terminal', 'wecom_error_41050', 'invalid welcome code',
                TIMESTAMPTZ '2026-07-24T11:31:32.223640+08:00', 1,
                TIMESTAMPTZ '2026-07-24T11:31:32.223640+08:00',
                TIMESTAMPTZ '2026-07-24T11:31:32.739066+08:00'
            )
            """,
            (EXPECTED_ATTEMPT_ID, EXPECTED_JOB_ID),
        )
        connection.execute(
            "UPDATE external_effect_job SET last_attempt_id = %s WHERE id = %s",
            (EXPECTED_ATTEMPT_ID, EXPECTED_JOB_ID),
        )
        connection.execute(
            """
            INSERT INTO channel_welcome_effect_graph (
                id, execution_id, idempotency_key, status, final_effect_job_id
            ) VALUES (%s, %s, 'production-welcome-timeout-graph-3', 'terminal', %s)
            """,
            (EXPECTED_GRAPH_ID, EXPECTED_BUSINESS_ID, EXPECTED_JOB_ID),
        )
        connection.commit()

    before = checks._external_effect_failed_retryable_backlog()
    assert before.status == "fail"
    assert before.evidence["failed_terminal_count"] == 1
    assert before.evidence["production_welcome_41050_acknowledgement"][
        "acknowledged_count"
    ] == 0

    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "releases"
        / "production_welcome_timeout_acknowledgement.json"
    )
    dry_run = acknowledge(
        manifest_path=manifest_path,
        release_sha="a" * 40,
        authorization_base_sha=AUTHORIZATION_BASE_SHA,
        confirmation=EXPECTED_CONFIRMATION,
        actor="pytest",
        reason="exact production welcome timeout no-replay test",
        apply=False,
    )
    assert dry_run == {
        "ok": True,
        "applied": False,
        "candidate_count": 1,
        "replay_prohibited": True,
        "provider_success_claimed": False,
        "real_external_call_executed": False,
        "target_values_redacted": True,
    }

    with pytest.raises(RuntimeError, match="AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED=1"):
        acknowledge(
            manifest_path=manifest_path,
            release_sha="a" * 40,
            authorization_base_sha=AUTHORIZATION_BASE_SHA,
            confirmation=EXPECTED_CONFIRMATION,
            actor="pytest",
            reason="authorization guard test",
            apply=True,
        )

    monkeypatch.setenv("AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED", "1")
    applied = acknowledge(
        manifest_path=manifest_path,
        release_sha="a" * 40,
        authorization_base_sha=AUTHORIZATION_BASE_SHA,
        confirmation=EXPECTED_CONFIRMATION,
        actor="pytest",
        reason="exact production welcome timeout no-replay test",
        apply=True,
    )
    assert applied == {
        "ok": True,
        "applied": True,
        "acknowledged_count": 1,
        "created_count": 1,
        "replay_prohibited": True,
        "provider_success_claimed": False,
        "real_external_call_executed": False,
        "target_values_redacted": True,
    }

    repeated = acknowledge(
        manifest_path=manifest_path,
        release_sha="b" * 40,
        authorization_base_sha=AUTHORIZATION_BASE_SHA,
        confirmation=EXPECTED_CONFIRMATION,
        actor="pytest",
        reason="idempotent production welcome timeout no-replay test",
        apply=True,
    )
    assert repeated["created_count"] == 0

    after = checks._external_effect_failed_retryable_backlog()
    assert after.status == "ok"
    assert after.evidence["failed_terminal_count"] == 0
    assert after.evidence["production_welcome_41050_acknowledgement"] == {
        "acknowledged_count": 1,
        "excluded_from_business_health": True,
        "operator_acknowledgement_required": True,
        "provider_success_claimed": False,
        "replay_prohibited": True,
        "strict_provenance_required": True,
    }

    with psycopg.connect(database_url) as connection:
        acknowledgement = connection.execute(
            """
            SELECT acknowledgement_type, graph_id, replay_prohibited,
                   provider_success_claimed,
                   evidence_json->>'real_external_call_executed_by_acknowledgement'
            FROM queue_terminal_acknowledgement
            WHERE job_id = %s
              AND evidence_json->>'target_hash_sha256' = ENCODE(
                  SHA256(
                      CONVERT_TO('external_user', 'UTF8')
                      || DECODE('00', 'hex')
                      || CONVERT_TO(%s, 'UTF8')
                  ),
                  'hex'
              )
            """,
            (EXPECTED_JOB_ID, target_id),
        ).fetchone()
        job_status = connection.execute(
            "SELECT status FROM external_effect_job WHERE id = %s",
            (EXPECTED_JOB_ID,),
        ).fetchone()[0]
        graph_status = connection.execute(
            "SELECT status FROM channel_welcome_effect_graph WHERE id = %s",
            (EXPECTED_GRAPH_ID,),
        ).fetchone()[0]
    assert acknowledgement == (
        "production_welcome_41050_job_2157_no_replay",
        EXPECTED_GRAPH_ID,
        True,
        False,
        "false",
    )
    assert job_status == "failed_terminal"
    assert graph_status == "terminal"

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO external_effect_attempt (
                attempt_id, job_id, adapter_name, adapter_mode, operation,
                status, error_code, provider_call_started_at, worker_generation,
                started_at, completed_at
            ) VALUES (
                'eea-unexpected-production-welcome-replay', %s,
                'wecom_welcome_message', 'execute', 'send', 'failed_terminal',
                'wecom_error_41050', CURRENT_TIMESTAMP, 1,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (EXPECTED_JOB_ID,),
        )
        connection.commit()

    after_unexpected_replay = checks._external_effect_failed_retryable_backlog()
    assert after_unexpected_replay.status == "fail"
    assert after_unexpected_replay.evidence["failed_terminal_count"] == 1
