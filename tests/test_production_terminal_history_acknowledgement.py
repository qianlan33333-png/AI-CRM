from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.postgres
def test_exact_production_terminal_histories_require_append_only_no_replay_acknowledgements(
    next_pg_schema,
    monkeypatch,
) -> None:
    import psycopg

    from aicrm_next.data_health import checks
    from scripts.ops.acknowledge_production_terminal_history import (
        PRIVATE_CONFIRMATION,
        REFUND_CONFIRMATION,
        acknowledge,
    )

    del next_pg_schema
    monkeypatch.setattr(checks, "EXTERNAL_EFFECT_TERMINAL_LOOKBACK_HOURS", 24 * 365 * 100)

    database_url = os.environ["DATABASE_URL"]
    suffix = uuid4().hex
    private_attempt_id = f"eea-private-84061-{suffix}"
    private_execution_id = f"exe-private-84061-{suffix}"
    refund_jobs: list[tuple[int, str]] = []
    acknowledgement_execution_ids = [private_execution_id]

    with psycopg.connect(database_url) as connection:
        private_job_id = int(
            connection.execute(
                """
                INSERT INTO external_effect_job (
                    effect_type, adapter_name, operation, target_type, target_id,
                    business_type, business_id, source_module, source_route,
                    idempotency_key, execution_mode, status, attempt_count,
                    max_attempts, last_error_code, last_error_message,
                    side_effect_executed, provider_result_received,
                    reconciliation_required, worker_generation, policy_version,
                    execution_id, created_at, updated_at, completed_at
                ) VALUES (
                    'wecom.message.private.send', 'wecom_private_message',
                    'send_private_message', 'external_user', %s,
                    'broadcast_job', %s,
                    'background_jobs.broadcast_effect_delegate',
                    'broadcast_effect_delegate', %s, 'execute', 'failed_terminal',
                    1, 5, 'external_call_failed_known',
                    'recipient is not external contact', TRUE, FALSE, FALSE, 1,
                    'queue-v2-production-all-g1', %s,
                    TIMESTAMPTZ '2026-07-22T19:42:20+08:00',
                    TIMESTAMPTZ '2026-07-22T19:42:26.789022+08:00',
                    TIMESTAMPTZ '2026-07-22T19:42:26.789022+08:00'
                )
                RETURNING id
                """,
                (
                    f"redacted-private-target-{suffix}",
                    f"broadcast-private-{suffix}",
                    f"private-84061-{suffix}",
                    private_execution_id,
                ),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO external_effect_attempt (
                attempt_id, job_id, adapter_name, adapter_mode, operation,
                status, error_code, error_message, worker_generation,
                started_at, completed_at
            ) VALUES (
                %s, %s, 'wecom_private_message', 'execute', 'send_private_message',
                'failed_terminal', 'external_call_failed_known',
                'recipient is not external contact', 1,
                TIMESTAMPTZ '2026-07-22T19:42:20+08:00',
                TIMESTAMPTZ '2026-07-22T19:42:26.789022+08:00'
            )
            """,
            (private_attempt_id, private_job_id),
        )
        connection.execute(
            "UPDATE external_effect_job SET last_attempt_id = %s WHERE id = %s",
            (private_attempt_id, private_job_id),
        )

        for index, second in enumerate((35, 44, 50), start=1):
            out_refund_no = f"WXR-NOT-ENOUGH-{suffix}-{index}"
            execution_id = f"exe-refund-not-enough-{suffix}-{index}"
            attempt_id = f"eea-refund-not-enough-{suffix}-{index}"
            job_id = int(
                connection.execute(
                    f"""
                    INSERT INTO external_effect_job (
                        effect_type, adapter_name, operation, target_type, target_id,
                        business_type, business_id, source_module, source_route,
                        idempotency_key, execution_mode, status, attempt_count,
                        max_attempts, last_error_code, last_error_message,
                        side_effect_executed, provider_result_received,
                        provider_call_started_at, reconciliation_required,
                        worker_generation, policy_version, execution_id,
                        created_at, updated_at, completed_at
                    ) VALUES (
                        'payment.wechat.refund.request', 'wechat_payment',
                        'refund_request', 'wechat_refund', %s,
                        'commerce_order', %s, 'commerce.admin_transactions',
                        'commerce.admin_transactions.create_wechat_refund_request',
                        %s, 'execute', 'failed_terminal', 1, 5,
                        'http_403', 'provider rejected refund', TRUE, TRUE,
                        TIMESTAMPTZ '2026-07-23T11:4{index}:30+08:00', FALSE, 1,
                        'queue-v2-production-all-g1', %s,
                        TIMESTAMPTZ '2026-07-23T11:4{index}:30+08:00',
                        TIMESTAMPTZ '2026-07-23T11:4{index}:{second:02d}+08:00',
                        TIMESTAMPTZ '2026-07-23T11:4{index}:{second:02d}+08:00'
                    )
                    RETURNING id
                    """,
                    (
                        out_refund_no,
                        f"commerce-order-{suffix}-{index}",
                        f"refund-not-enough-{suffix}-{index}",
                        execution_id,
                    ),
                ).fetchone()[0]
            )
            connection.execute(
                f"""
                INSERT INTO external_effect_attempt (
                    attempt_id, job_id, adapter_name, adapter_mode, operation,
                    status, error_code, error_message, provider_call_started_at,
                    worker_generation, started_at, completed_at
                ) VALUES (
                    %s, %s, 'wechat_payment', 'execute', 'refund_request',
                    'failed_terminal', 'http_403', 'provider rejected refund',
                    TIMESTAMPTZ '2026-07-23T11:4{index}:30+08:00', 1,
                    TIMESTAMPTZ '2026-07-23T11:4{index}:30+08:00',
                    TIMESTAMPTZ '2026-07-23T11:4{index}:{second:02d}+08:00'
                )
                """,
                (attempt_id, job_id),
            )
            connection.execute(
                "UPDATE external_effect_job SET last_attempt_id = %s WHERE id = %s",
                (attempt_id, job_id),
            )
            connection.execute(
                """
                INSERT INTO wechat_pay_refunds (
                    out_refund_no, status, response_payload_json
                ) VALUES (
                    %s, 'failed', '{"provider_payload":{"code":"NOT_ENOUGH"}}'::jsonb
                )
                """,
                (out_refund_no,),
            )
            refund_jobs.append((job_id, attempt_id))
            acknowledgement_execution_ids.append(execution_id)
        connection.commit()

    without_acknowledgement = checks._external_effect_failed_retryable_backlog()
    assert without_acknowledgement.status == "fail"
    assert without_acknowledgement.evidence["failed_terminal_count"] == 4

    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "releases"
        / "production_terminal_history_acknowledgements.json"
    )
    dry_run = acknowledge(
        manifest_path=manifest_path,
        release_sha="a" * 40,
        authorization_base_sha="8ab2f80ec8a6808a357a5911ace38128599a3d3d",
        private_confirmation=PRIVATE_CONFIRMATION,
        refund_confirmation=REFUND_CONFIRMATION,
        actor="pytest",
        reason="exact production no-replay history test",
        apply=False,
    )
    assert dry_run["private_message_acknowledged_count"] == 1
    assert dry_run["refund_acknowledged_count"] == 3
    assert dry_run["private_message_created_count"] == 0
    assert dry_run["refund_created_count"] == 0

    monkeypatch.setenv("AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED", "1")
    applied = acknowledge(
        manifest_path=manifest_path,
        release_sha="a" * 40,
        authorization_base_sha="8ab2f80ec8a6808a357a5911ace38128599a3d3d",
        private_confirmation=PRIVATE_CONFIRMATION,
        refund_confirmation=REFUND_CONFIRMATION,
        actor="pytest",
        reason="exact production no-replay history test",
        apply=True,
    )
    assert applied == {
        "ok": True,
        "applied": True,
        "private_message_acknowledged_count": 1,
        "private_message_created_count": 1,
        "refund_acknowledged_count": 3,
        "refund_created_count": 3,
        "replay_prohibited": True,
        "provider_success_claimed": False,
        "real_external_call_executed": False,
        "target_values_redacted": True,
    }

    repeated = acknowledge(
        manifest_path=manifest_path,
        release_sha="b" * 40,
        authorization_base_sha="8ab2f80ec8a6808a357a5911ace38128599a3d3d",
        private_confirmation=PRIVATE_CONFIRMATION,
        refund_confirmation=REFUND_CONFIRMATION,
        actor="pytest",
        reason="idempotent production no-replay history test",
        apply=True,
    )
    assert repeated["private_message_created_count"] == 0
    assert repeated["refund_created_count"] == 0

    # The acknowledgement is an append-only historical fact.  Later job
    # bookkeeping must not force every deployment to rediscover the original
    # authorization-window candidates before it can validate the same records.
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ANY(%s)
            """,
            ([job_id for job_id, _ in refund_jobs],),
        )
        connection.commit()

    repeated_after_candidate_drift = acknowledge(
        manifest_path=manifest_path,
        release_sha="c" * 40,
        authorization_base_sha="8ab2f80ec8a6808a357a5911ace38128599a3d3d",
        private_confirmation=PRIVATE_CONFIRMATION,
        refund_confirmation=REFUND_CONFIRMATION,
        actor="pytest",
        reason="idempotent acknowledgement after candidate drift",
        apply=True,
    )
    assert repeated_after_candidate_drift["private_message_acknowledged_count"] == 1
    assert repeated_after_candidate_drift["private_message_created_count"] == 0
    assert repeated_after_candidate_drift["refund_acknowledged_count"] == 3
    assert repeated_after_candidate_drift["refund_created_count"] == 0

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET updated_at = completed_at
            WHERE id = ANY(%s)
            """,
            ([job_id for job_id, _ in refund_jobs],),
        )
        connection.commit()

    health = checks._external_effect_failed_retryable_backlog()
    assert health.status == "ok"
    assert health.evidence["failed_terminal_count"] == 0
    assert health.evidence["production_private_message_84061_acknowledgement"][
        "acknowledged_count"
    ] == 1
    assert health.evidence["production_wechat_refund_not_enough_acknowledgement"][
        "acknowledged_count"
    ] == 3

    with psycopg.connect(database_url) as connection:
        acknowledgements = connection.execute(
            """
            SELECT acknowledgement_type, COUNT(*),
                   BOOL_AND(replay_prohibited), BOOL_OR(provider_success_claimed),
                   BOOL_AND(graph_id IS NULL)
            FROM queue_terminal_acknowledgement
            WHERE job_execution_id = ANY(%s)
            GROUP BY acknowledgement_type
            ORDER BY acknowledgement_type
            """,
            (acknowledgement_execution_ids,),
        ).fetchall()
        terminal_jobs = connection.execute(
            """
            SELECT COUNT(*) FROM external_effect_job
            WHERE id = ANY(%s) AND status = 'failed_terminal'
            """,
            ([private_job_id, *(job_id for job_id, _ in refund_jobs)],),
        ).fetchone()[0]
    assert acknowledgements == [
        ("production_private_message_84061_no_replay", 1, True, False, True),
        ("production_wechat_refund_not_enough_no_replay", 3, True, False, True),
    ]
    assert terminal_jobs == 4

    with psycopg.connect(database_url) as connection:
        replay_attempt_id = f"eea-refund-unexpected-replay-{suffix}"
        connection.execute(
            """
            INSERT INTO external_effect_attempt (
                attempt_id, job_id, adapter_name, adapter_mode, operation,
                status, error_code, provider_call_started_at, worker_generation,
                started_at, completed_at
            ) VALUES (
                %s, %s, 'wechat_payment', 'execute', 'refund_request',
                'failed_terminal', 'http_403', CURRENT_TIMESTAMP, 1,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (replay_attempt_id, refund_jobs[0][0]),
        )
        connection.commit()

    after_unexpected_replay = checks._external_effect_failed_retryable_backlog()
    assert after_unexpected_replay.status == "fail"
    assert after_unexpected_replay.evidence["failed_terminal_count"] == 1

    with pytest.raises(RuntimeError, match="failed durable linkage validation"):
        acknowledge(
            manifest_path=manifest_path,
            release_sha="d" * 40,
            authorization_base_sha="8ab2f80ec8a6808a357a5911ace38128599a3d3d",
            private_confirmation=PRIVATE_CONFIRMATION,
            refund_confirmation=REFUND_CONFIRMATION,
            actor="pytest",
            reason="unexpected replay must fail acknowledgement validation",
            apply=True,
        )
