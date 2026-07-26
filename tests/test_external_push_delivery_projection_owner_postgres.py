from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from aicrm_next.external_push.delivery_projection_port import build_external_push_delivery_projection_port


def test_delivery_projection_owner_is_atomic_and_idempotent_in_real_postgres(next_pg_schema) -> None:
    port = build_external_push_delivery_projection_port()
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        config_id = connection.execute(
            """
            INSERT INTO external_push_config (
                tenant_id, target_type, target_id, event_type, enabled, webhook_url
            ) VALUES ('aicrm', 'product', '910044', 'transaction.paid', TRUE, 'https://example.test/hook')
            RETURNING id
            """
        ).fetchone()["id"]
        first = port.create_order_delivery_once_dbapi(
            connection,
            tenant_id="aicrm",
            config_id=config_id,
            event_type="transaction.paid",
            target_id="910044",
            order_id=920044,
            product_id=910044,
            request_url="https://example.test/hook",
        )
        second = port.create_order_delivery_once_dbapi(
            connection,
            tenant_id="aicrm",
            config_id=config_id,
            event_type="transaction.paid",
            target_id="910044",
            order_id=920044,
            product_id=910044,
            request_url="https://example.test/hook",
        )
        updated = port.update_delivery_result_dbapi(
            connection,
            delivery_id=first["delivery_id"],
            status="retrying",
            attempt_count=1,
            request_url="https://example.test/hook",
            request_headers={"Authorization": "[redacted]"},
            request_body={"phone_number": "[pii]"},
            response_status=None,
            response_body='{"external_effect_job_id":930044}',
            error_message="external_effect_job_queued",
            next_retry_at="",
        )
        reconciled = port.reconcile_succeeded_dbapi(
            connection,
            delivery_id=first["delivery_id"],
            external_effect_job_id=930044,
            response_status=204,
            actor_hash="actor-hash",
            reason_hash="reason-hash",
        )
        reconciled_again = port.reconcile_succeeded_dbapi(
            connection,
            delivery_id=first["delivery_id"],
            external_effect_job_id=930044,
            response_status=204,
            actor_hash="actor-hash",
            reason_hash="reason-hash",
        )
        persisted = connection.execute(
            "SELECT * FROM external_push_delivery WHERE delivery_id = %s",
            (first["delivery_id"],),
        ).fetchone()
        count = connection.execute(
            "SELECT count(*) AS count FROM external_push_delivery WHERE order_id = %s",
            (920044,),
        ).fetchone()["count"]
        connection.rollback()

    assert first["id"] == second["id"]
    assert updated["status"] == "retrying"
    assert reconciled is True
    assert reconciled_again is False
    assert count == 1
    assert persisted["status"] == "success"
    assert persisted["attempt_count"] == 1
    assert persisted["response_status"] == 204
    assert '"reconciled":true' in persisted["response_body"]
