from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest


class _PrivateMessageContactAbsenceSession:
    def __init__(self) -> None:
        self.rollback_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def rollback(self) -> None:
        self.rollback_count += 1


def _acknowledge_with_rows(monkeypatch, *, rows: list[dict]):
    from scripts.ops import (
        acknowledge_production_private_message_contact_absence as contact_absence,
    )

    session = _PrivateMessageContactAbsenceSession()
    monkeypatch.setattr(contact_absence, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(contact_absence, "_existing_rows", lambda _session: [])
    monkeypatch.setattr(
        contact_absence,
        "_candidate_rows",
        lambda _session, _authorization: rows,
    )
    monkeypatch.setenv("AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED", "1")
    result = contact_absence.acknowledge(
        manifest_path=(
            Path(__file__).resolve().parents[1]
            / "docs"
            / "releases"
            / "production_private_message_contact_absence_20260728_acknowledgement.json"
        ),
        release_sha="a" * 40,
        authorization_base_sha=contact_absence.AUTHORIZATION_BASE_SHA,
        confirmation=contact_absence.EXPECTED_CONFIRMATION,
        actor="pytest",
        reason="private-message contact-absence history idempotency boundary test",
        apply=True,
    )
    return result, session


def test_absent_private_message_contact_absence_histories_are_an_idempotent_noop(
    monkeypatch,
) -> None:
    result, session = _acknowledge_with_rows(monkeypatch, rows=[])

    assert result == {
        "ok": True,
        "applied": False,
        "acknowledged_count": 0,
        "created_count": 0,
        "no_op_reason": "authorized_historical_terminals_absent",
        "replay_prohibited": True,
        "provider_success_claimed": False,
        "real_external_call_executed": False,
        "target_values_redacted": True,
    }
    assert session.rollback_count == 1


def test_partial_private_message_contact_absence_history_remains_rejected(
    monkeypatch,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="expected exactly 3 authorized production private-message terminals; found 1",
    ):
        _acknowledge_with_rows(monkeypatch, rows=[{}])


@pytest.mark.postgres
def test_absent_private_message_contact_absence_histories_are_a_postgres_noop(
    next_pg_schema,
    monkeypatch,
) -> None:
    from scripts.ops import (
        acknowledge_production_private_message_contact_absence as contact_absence,
    )

    del next_pg_schema
    monkeypatch.setenv("AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED", "1")
    result = contact_absence.acknowledge(
        manifest_path=(
            Path(__file__).resolve().parents[1]
            / "docs"
            / "releases"
            / "production_private_message_contact_absence_20260728_acknowledgement.json"
        ),
        release_sha="a" * 40,
        authorization_base_sha=contact_absence.AUTHORIZATION_BASE_SHA,
        confirmation=contact_absence.EXPECTED_CONFIRMATION,
        actor="pytest",
        reason="postgres absent private-message contact-absence history no-op",
        apply=True,
    )

    assert result["applied"] is False
    assert result["acknowledged_count"] == 0
    assert result["created_count"] == 0
    assert result["no_op_reason"] == "authorized_historical_terminals_absent"
    assert result["real_external_call_executed"] is False


@pytest.mark.postgres
def test_exact_private_message_contact_absence_terminals_are_acknowledged_without_replay(
    next_pg_schema,
    monkeypatch,
) -> None:
    import psycopg

    from aicrm_next.insights.data_health import checks
    from scripts.ops.acknowledge_production_private_message_contact_absence import (
        AUTHORIZATION_BASE_SHA,
        EXPECTED_CONFIRMATION,
        acknowledge,
    )

    del next_pg_schema
    monkeypatch.setattr(checks, "EXTERNAL_EFFECT_TERMINAL_LOOKBACK_HOURS", 24 * 365 * 100)
    database_url = os.environ["DATABASE_URL"]
    suffix = uuid4().hex
    job_ids: list[int] = []
    execution_ids: list[str] = []

    with psycopg.connect(database_url) as connection:
        for index, completed_at in enumerate(
            (
                "2026-07-28T16:27:28.155349+08:00",
                "2026-07-28T16:45:00.123456+08:00",
                "2026-07-28T17:02:58.935555+08:00",
            ),
            start=1,
        ):
            execution_id = f"exe-private-contact-absence-{suffix}-{index}"
            attempt_id = f"eea-private-contact-absence-{suffix}-{index}"
            business_id = f"broadcast-private-contact-absence-{suffix}-{index}"
            job_id = int(
                connection.execute(
                    """
                    INSERT INTO external_effect_job (
                        effect_type, adapter_name, operation, target_type, target_id,
                        business_type, business_id, source_module, source_route,
                        idempotency_key, actor_type, risk_level, execution_mode, lane,
                        status, attempt_count, max_attempts, last_attempt_id,
                        last_error_code, last_error_message,
                        side_effect_executed, provider_result_received,
                        provider_call_started_at, reconciliation_required,
                        worker_generation, policy_version, execution_id,
                        created_at, updated_at, completed_at
                    ) VALUES (
                        'wecom.message.private.send', 'wecom_private_message',
                        'send_private_message', 'external_contact', %s,
                        'broadcast_job', %s,
                        'background_jobs.broadcast_effect_delegate',
                        'broadcast_effect_delegate', %s, 'system', 'medium',
                        'execute', 'wecom_bulk', 'failed_terminal', 1, 5, %s,
                        'external_contact_relationship_absent',
                        'external contact relationship absent', TRUE, TRUE,
                        (%s::timestamptz - INTERVAL '1 second'), FALSE, 1,
                        'queue-v2-production-all-g1', %s,
                        (%s::timestamptz - INTERVAL '2 seconds'),
                        %s::timestamptz, %s::timestamptz
                    )
                    RETURNING id
                    """,
                    (
                        f"redacted-private-target-{suffix}-{index}",
                        business_id,
                        f"private-contact-absence-{suffix}-{index}",
                        attempt_id,
                        completed_at,
                        execution_id,
                        completed_at,
                        completed_at,
                        completed_at,
                    ),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO external_effect_attempt (
                    attempt_id, job_id, adapter_name, adapter_mode, operation,
                    status, response_summary_json, error_code, error_message,
                    provider_call_started_at, worker_generation, started_at, completed_at
                ) VALUES (
                    %s, %s, 'wecom_private_message', 'execute',
                    'send_private_message', 'failed_terminal',
                    '{"errcode":84061,"real_external_call_executed":true}'::jsonb,
                    'external_contact_relationship_absent',
                    'external contact relationship absent',
                    (%s::timestamptz - INTERVAL '1 second'), 1,
                    (%s::timestamptz - INTERVAL '1 second'), %s::timestamptz
                )
                """,
                (attempt_id, job_id, completed_at, completed_at, completed_at),
            )
            job_ids.append(job_id)
            execution_ids.append(execution_id)
        connection.commit()

    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "releases"
        / "production_private_message_contact_absence_20260728_acknowledgement.json"
    )
    before = checks._external_effect_failed_retryable_backlog()
    assert before.status == "ok"
    assert before.evidence[
        "production_private_message_contact_absence_20260728_acknowledgement"
    ]["acknowledged_count"] == 0
    assert before.evidence["private_message_contact_relationship_absent"]["count"] == 3

    dry_run = acknowledge(
        manifest_path=manifest_path,
        release_sha="a" * 40,
        authorization_base_sha=AUTHORIZATION_BASE_SHA,
        confirmation=EXPECTED_CONFIRMATION,
        actor="pytest",
        reason="exact private-message contact-absence no-replay test",
        apply=False,
    )
    assert dry_run == {
        "ok": True,
        "applied": False,
        "acknowledged_count": 3,
        "created_count": 0,
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
        reason="exact private-message contact-absence no-replay test",
        apply=True,
    )
    assert applied["acknowledged_count"] == 3
    assert applied["created_count"] == 3

    repeated = acknowledge(
        manifest_path=manifest_path,
        release_sha="b" * 40,
        authorization_base_sha=AUTHORIZATION_BASE_SHA,
        confirmation=EXPECTED_CONFIRMATION,
        actor="pytest",
        reason="idempotent private-message contact-absence no-replay test",
        apply=True,
    )
    assert repeated["acknowledged_count"] == 3
    assert repeated["created_count"] == 0

    after = checks._external_effect_failed_retryable_backlog()
    assert after.status == "ok"
    assert after.evidence[
        "production_private_message_contact_absence_20260728_acknowledgement"
    ] == {
        "acknowledged_count": 3,
        "excluded_from_business_health": True,
        "operator_acknowledgement_required": True,
        "provider_success_claimed": False,
        "replay_prohibited": True,
        "strict_provenance_required": True,
    }

    with psycopg.connect(database_url) as connection:
        acknowledgement = connection.execute(
            """
            SELECT COUNT(*), BOOL_AND(replay_prohibited),
                   BOOL_OR(provider_success_claimed),
                   BOOL_AND(graph_id IS NULL),
                   BOOL_AND(evidence_json->>'real_external_call_executed' = 'true'),
                   BOOL_AND(
                       evidence_json->>'real_external_call_executed_by_acknowledgement' = 'false'
                   )
            FROM queue_terminal_acknowledgement
            WHERE acknowledgement_type =
                'production_private_message_contact_absence_20260728_no_replay'
              AND job_execution_id = ANY(%s)
            """,
            (execution_ids,),
        ).fetchone()
        terminal_count = connection.execute(
            """
            SELECT COUNT(*) FROM external_effect_job
            WHERE id = ANY(%s) AND status = 'failed_terminal'
            """,
            (job_ids,),
        ).fetchone()[0]
    assert acknowledgement == (3, True, False, True, True, True)
    assert terminal_count == 3

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO external_effect_attempt (
                attempt_id, job_id, adapter_name, adapter_mode, operation,
                status, response_summary_json, error_code,
                provider_call_started_at, worker_generation, started_at, completed_at
            ) VALUES (
                %s, %s, 'wecom_private_message', 'execute',
                'send_private_message', 'failed_terminal',
                '{"errcode":84061,"real_external_call_executed":true}'::jsonb,
                'external_contact_relationship_absent', CURRENT_TIMESTAMP, 1,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (f"eea-unexpected-replay-{suffix}", job_ids[0]),
        )
        connection.commit()

    unsafe = checks._external_effect_failed_retryable_backlog()
    assert unsafe.status == "fail"
    assert unsafe.evidence["failed_terminal_count"] == 1
    with pytest.raises(RuntimeError, match="failed durable linkage validation"):
        acknowledge(
            manifest_path=manifest_path,
            release_sha="c" * 40,
            authorization_base_sha=AUTHORIZATION_BASE_SHA,
            confirmation=EXPECTED_CONFIRMATION,
            actor="pytest",
            reason="unexpected replay must fail durable acknowledgement validation",
            apply=True,
        )
